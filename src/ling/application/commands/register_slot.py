"""Register a slot as a local Ling record."""

from __future__ import annotations

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
from ling.application.dto import CONFLICT, INVALID_INPUT, RegisterSlotCommand, RegisterSlotResult
from ling.application.ports.attachments import SlotCredential
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import Slot, template_catalog


def execute(
    command: RegisterSlotCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> RegisterSlotResult:
    """Validate the template and save the slot. No external registration is required."""

    prepared = prepare_operation(
        uow,
        ids,
        clock,
        command.operation_id,
        "register_slot",
        {
            "slot_id": command.slot_id,
            "template_id": command.template_id,
            "token_hash": command.token_hash,
        },
        RegisterSlotResult,
    )
    if prepared.invalid or prepared.conflict or prepared.replay is not None:
        uow.rollback()
        if isinstance(prepared.replay, RegisterSlotResult):
            return prepared.replay
        return RegisterSlotResult(
            ok=False,
            operation_id=prepared.operation_id,
            occurred_at=prepared.occurred_at,
            error_code=INVALID_INPUT if prepared.invalid else CONFLICT,
            message=(
                "operation id must be a non-empty string"
                if prepared.invalid
                else "operation id was already used for a different request"
            ),
        )
    operation_id = prepared.operation_id
    occurred_at = prepared.occurred_at
    slot_id = parse_slot_id(command.slot_id)
    template_id = parse_template_id(command.template_id)
    if slot_id is None or template_id is None or not is_token_hash(command.token_hash):
        uow.rollback()
        return RegisterSlotResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            error_code=INVALID_INPUT,
            message="slot id, template id, and attachment token are required",
        )
    if not known_template(template_id):
        uow.rollback()
        code, message = not_found(f"unknown template {template_id.value}")
        return RegisterSlotResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            error_code=code,
            message=message,
        )
    existing = load_slot(uow, slot_id)
    credential = uow.credentials.get(slot_id.value)
    if existing is not None and (
        existing.template.template_id != template_id or credential is not None
    ):
        uow.rollback()
        return RegisterSlotResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            slot_id=slot_id.value,
            error_code=CONFLICT,
            message=f"slot {slot_id.value} is already registered",
        )
    template = template_catalog()[template_id]
    if existing is None:
        uow.slots.save(Slot(slot_id=slot_id, template=template))
    uow.credentials.save(
        SlotCredential(
            slot_id=slot_id.value,
            token_hash=command.token_hash,
            created_at=occurred_at,
        )
    )
    result = RegisterSlotResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        slot_id=slot_id.value,
        template_id=template.template_id.value,
        message="provisioned" if existing is not None else "registered",
    )
    published = commit_operation(uow, prepared, result)
    if isinstance(published, RegisterSlotResult):
        return published
    return RegisterSlotResult(
        ok=False,
        operation_id=operation_id,
        occurred_at=occurred_at,
        error_code=CONFLICT,
        message="operation id was already used for a different request",
    )
