"""Provision or rotate a slot credential while the commander holds the lease."""

from __future__ import annotations

from datetime import datetime

from ling.application.attachments import is_token_hash
from ling.application.commands.support import (
    commit_operation,
    known_template,
    load_slot,
    not_found,
    parse_slot_id,
    parse_template_id,
    prepare_operation,
)
from ling.application.controller_lease import (
    CONTROLLER_SLOT_ID,
    commander_is_attached,
    lease_is_current,
    operation_label,
)
from ling.application.dto import (
    CONFLICT,
    FORBIDDEN,
    INVALID_INPUT,
    LEASE_REQUIRED,
    ProvisionSlotCommand,
    ProvisionSlotResult,
)
from ling.application.ports.attachments import SlotCredential
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import Slot, template_catalog

_FORBIDDEN = "attachment credentials are administrator-controlled"
_REQUIRED = "controller lease required"
_CONFLICT = "operation id was already used for a different request"
_INVALID_OPERATION = "operation id must be a non-empty string"
_INPUT = "slot id, template id, and attachment token are required"


def execute(
    command: ProvisionSlotCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> ProvisionSlotResult:
    """Write one credential hash. Callers that are not the lease holder change nothing."""

    occurred_at = clock.now()
    if not commander_is_attached(uow, command.actor_slot_id, command.session_id, occurred_at):
        uow.rollback()
        return _denied(ids, command.operation_id, occurred_at, FORBIDDEN, _FORBIDDEN)
    prepared = prepare_operation(
        uow,
        ids,
        clock,
        command.operation_id,
        "provision_slot",
        {
            "actor_slot_id": CONTROLLER_SLOT_ID,
            "session_id": command.session_id,
            "slot_id": command.slot_id,
            "template_id": command.template_id,
            "token_hash": command.token_hash,
        },
        ProvisionSlotResult,
    )
    if prepared.invalid or prepared.conflict or prepared.replay is not None:
        uow.rollback()
        if isinstance(prepared.replay, ProvisionSlotResult):
            return prepared.replay
        return _result(
            prepared.operation_id,
            prepared.occurred_at,
            INVALID_INPUT if prepared.invalid else CONFLICT,
            _INVALID_OPERATION if prepared.invalid else _CONFLICT,
        )
    occurred_at = prepared.occurred_at
    active = uow.controller_leases.get_active()
    if (
        active is None
        or not lease_is_current(active, uow, occurred_at)
        or active.session_id != command.session_id
    ):
        uow.rollback()
        return _result(prepared.operation_id, occurred_at, LEASE_REQUIRED, _REQUIRED)
    slot_id = parse_slot_id(command.slot_id)
    template_id = parse_template_id(command.template_id)
    if slot_id is None or template_id is None or not is_token_hash(command.token_hash):
        uow.rollback()
        return _result(prepared.operation_id, occurred_at, INVALID_INPUT, _INPUT)
    if not known_template(template_id):
        uow.rollback()
        code, message = not_found(f"unknown template {template_id.value}")
        return _result(prepared.operation_id, occurred_at, code, message)
    existing = load_slot(uow, slot_id)
    if existing is not None and existing.template.template_id != template_id:
        uow.rollback()
        return ProvisionSlotResult(
            ok=False,
            operation_id=prepared.operation_id,
            occurred_at=occurred_at,
            slot_id=slot_id.value,
            error_code=CONFLICT,
            message=f"slot {slot_id.value} is already registered",
        )
    credential = uow.credentials.get(slot_id.value) if existing is not None else None
    if existing is None:
        uow.slots.save(Slot(slot_id=slot_id, template=template_catalog()[template_id]))
    uow.credentials.save(
        SlotCredential(
            slot_id=slot_id.value,
            token_hash=command.token_hash,
            created_at=occurred_at,
        )
    )
    published = commit_operation(
        uow,
        prepared,
        ProvisionSlotResult(
            ok=True,
            operation_id=prepared.operation_id,
            occurred_at=occurred_at,
            slot_id=slot_id.value,
            template_id=template_id.value,
            message="rotated" if credential is not None else "provisioned",
        ),
    )
    if isinstance(published, ProvisionSlotResult):
        return published
    return _result(prepared.operation_id, occurred_at, CONFLICT, _CONFLICT)


def _denied(
    ids: IdGenerator,
    supplied: object,
    occurred_at: datetime,
    code: str,
    message: str,
) -> ProvisionSlotResult:
    return _result(operation_label(ids, supplied), occurred_at, code, message)


def _result(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
) -> ProvisionSlotResult:
    return ProvisionSlotResult(
        ok=False,
        operation_id=operation_id,
        occurred_at=occurred_at,
        error_code=code,
        message=message,
    )
