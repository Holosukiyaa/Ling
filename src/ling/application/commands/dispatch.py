"""Dispatch a ticket when the mentor template may manage the target template.

`target_slot_id` is optional. Omit it for template-only dispatch. When it is
set, the slot must exist and use that same template. Offline slots may receive
tickets.
"""

from __future__ import annotations

from datetime import datetime

from ling.application.commands.support import (
    commit_operation,
    domain_code,
    known_template,
    load_slot,
    not_found,
    parse_slot_id,
    parse_template_id,
    prepare_operation,
)
from ling.application.dto import (
    CONFLICT,
    CONSUMPTION_LOCK_HELD,
    FORBIDDEN,
    INVALID_INPUT,
    DispatchCommand,
    DispatchResult,
)
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import template_catalog
from ling.domain.agents.policy import can_manage
from ling.domain.agents.values import SlotId
from ling.domain.errors import DomainError
from ling.domain.locks import ConsumptionLock
from ling.domain.tickets.entities import Ticket, TicketId


def execute(
    command: DispatchCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> DispatchResult:
    """Occupy the consumption lock and store a queued ticket in one commit."""

    prepared = prepare_operation(
        uow,
        ids,
        clock,
        command.operation_id,
        "dispatch",
        {
            "issuer_slot_id": command.issuer_slot_id,
            "target_template_id": command.target_template_id,
            "target_slot_id": command.target_slot_id,
            "content": command.content,
        },
        DispatchResult,
    )
    if prepared.invalid or prepared.conflict or prepared.replay is not None:
        uow.rollback()
        if isinstance(prepared.replay, DispatchResult):
            return prepared.replay
        return _refused(
            prepared.operation_id,
            prepared.occurred_at,
            INVALID_INPUT if prepared.invalid else CONFLICT,
            (
                "operation id must be a non-empty string"
                if prepared.invalid
                else "operation id was already used for a different request"
            ),
        )
    operation_id = prepared.operation_id
    occurred_at = prepared.occurred_at
    issuer_id = parse_slot_id(command.issuer_slot_id)
    target_id = parse_template_id(command.target_template_id)
    if issuer_id is None or target_id is None or not isinstance(command.content, str):
        uow.rollback()
        return _refused(
            operation_id,
            occurred_at,
            INVALID_INPUT,
            "issuer, target template, and content are required",
        )
    if not command.content.strip():
        uow.rollback()
        return _refused(operation_id, occurred_at, INVALID_INPUT, "ticket content is empty")
    bound_slot, slot_error = _optional_target_slot(command.target_slot_id)
    if slot_error is not None:
        uow.rollback()
        return _refused(operation_id, occurred_at, INVALID_INPUT, slot_error)
    issuer = load_slot(uow, issuer_id)
    if issuer is None:
        uow.rollback()
        code, message = not_found(f"slot {issuer_id.value} is not registered")
        return _refused(operation_id, occurred_at, code, message)
    if not known_template(target_id):
        uow.rollback()
        code, message = not_found(f"unknown template {target_id.value}")
        return _refused(operation_id, occurred_at, code, message)
    target_slot = None
    if bound_slot is not None:
        target_slot = load_slot(uow, bound_slot)
        if target_slot is None:
            uow.rollback()
            code, message = not_found(f"slot {bound_slot.value} is not registered")
            return _refused(operation_id, occurred_at, code, message)
        if target_slot.template.template_id != target_id:
            uow.rollback()
            return _refused(
                operation_id,
                occurred_at,
                INVALID_INPUT,
                (
                    f"slot {bound_slot.value} is template "
                    f"{target_slot.template.template_id.value}, not {target_id.value}"
                ),
            )
    target = template_catalog()[target_id]
    if not can_manage(issuer.template, target):
        uow.rollback()
        return _refused(
            operation_id,
            occurred_at,
            FORBIDDEN,
            f"{issuer.template.template_id.value} cannot dispatch to {target.template_id.value}",
        )
    lock = uow.consumption_locks.get(issuer.slot_id)
    if lock is None:
        lock = ConsumptionLock(issuer.slot_id)
    if lock.held:
        uow.rollback()
        held_for = lock.ticket_id.value if lock.ticket_id is not None else "a ticket"
        return _refused(
            operation_id,
            occurred_at,
            CONSUMPTION_LOCK_HELD,
            f"{issuer.slot_id.value} still holds {held_for}",
        )
    ticket = Ticket(
        ticket_id=TicketId(ids.new_ticket_id()),
        issuer=issuer.slot_id,
        content=command.content,
        target_slot_id=None if target_slot is None else target_slot.slot_id,
    )
    try:
        lock.occupy(ticket.ticket_id)
    except DomainError as exc:
        uow.rollback()
        return _refused(operation_id, occurred_at, domain_code(exc), exc.message)
    uow.tickets.save(ticket)
    uow.consumption_locks.save(lock)
    queue = ticket.queue
    result = DispatchResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        ticket_id=ticket.ticket_id.value,
        state=ticket.state.value,
        queue=None if queue is None else queue.number,
        message="dispatched",
    )
    published = commit_operation(uow, prepared, result)
    if isinstance(published, DispatchResult):
        return published
    return _refused(
        operation_id,
        occurred_at,
        CONFLICT,
        "operation id was already used for a different request",
    )


def _optional_target_slot(value: str | None) -> tuple[SlotId | None, str | None]:
    """Return the bound slot, or an invalid-input message when the value is unusable."""

    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "target slot id must be a non-empty string"
    parsed = parse_slot_id(value)
    if parsed is None:
        return None, "target slot id must be a non-empty string"
    return parsed, None


def _refused(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
) -> DispatchResult:
    return DispatchResult(
        ok=False,
        operation_id=operation_id,
        occurred_at=occurred_at,
        error_code=code,
        message=message,
    )
