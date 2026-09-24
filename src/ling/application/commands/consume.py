"""Consume a reviewed ticket and release the issuer's consumption lock together."""

from __future__ import annotations

from datetime import datetime

from ling.application.commands.support import (
    domain_code,
    load_slot,
    load_ticket,
    not_found,
    parse_slot_id,
    parse_ticket_id,
)
from ling.application.dto import NOT_ISSUER, INVALID_INPUT, ConsumeCommand, ConsumeResult
from ling.application.ports.clock import Clock
from ling.application.ports.coordinator import CoordinatorPort
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.errors import DomainError
from ling.domain.tickets.entities import Ticket


def execute(
    command: ConsumeCommand,
    *,
    uow: UnitOfWork,
    coordinator: CoordinatorPort,
    ids: IdGenerator,
    clock: Clock,
) -> ConsumeResult:
    """Let the issuing mentor consume. Ticket and lock commit in one unit of work."""

    del coordinator
    operation_id = ids.new_operation_id()
    occurred_at = clock.now()
    actor_id = parse_slot_id(command.actor_slot_id)
    ticket_id = parse_ticket_id(command.ticket_id)
    if actor_id is None or ticket_id is None:
        uow.rollback()
        return _plain(
            operation_id,
            occurred_at,
            INVALID_INPUT,
            "actor slot id and ticket id must be non-empty strings",
            ticket_id=None,
        )
    actor = load_slot(uow, actor_id)
    if actor is None:
        uow.rollback()
        code, message = not_found(f"slot {actor_id.value} is not registered")
        return _plain(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    ticket = load_ticket(uow, ticket_id)
    if ticket is None:
        uow.rollback()
        code, message = not_found(f"ticket {ticket_id.value} does not exist")
        return _plain(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    if ticket.issuer != actor.slot_id:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            lock_held=_held(uow, ticket),
            ok=False,
            error_code=NOT_ISSUER,
            message=f"{actor.slot_id.value} did not issue {ticket.ticket_id.value}",
        )
    lock = uow.consumption_locks.get(actor.slot_id)
    if lock is None:
        uow.rollback()
        code, message = not_found(f"no consumption lock for {actor.slot_id.value}")
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            lock_held=False,
            ok=False,
            error_code=code,
            message=message,
        )
    try:
        lock.consume(ticket, actor.slot_id)
    except DomainError as exc:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            lock_held=lock.held,
            ok=False,
            error_code=domain_code(exc),
            message=exc.message,
        )
    uow.tickets.save(ticket)
    uow.consumption_locks.save(lock)
    uow.commit()
    return _snapshot(
        operation_id,
        occurred_at,
        ticket,
        lock_held=lock.held,
        ok=True,
        message="consumed",
    )


def _held(uow: UnitOfWork, ticket: Ticket) -> bool | None:
    lock = uow.consumption_locks.get(ticket.issuer)
    if lock is None:
        return None
    return lock.held


def _plain(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
    *,
    ticket_id: str | None,
) -> ConsumeResult:
    return ConsumeResult(
        ok=False,
        operation_id=operation_id,
        occurred_at=occurred_at,
        ticket_id=ticket_id,
        error_code=code,
        message=message,
    )


def _snapshot(
    operation_id: str,
    occurred_at: datetime,
    ticket: Ticket,
    *,
    lock_held: bool | None,
    ok: bool,
    message: str,
    error_code: str | None = None,
) -> ConsumeResult:
    queue = ticket.queue
    return ConsumeResult(
        ok=ok,
        operation_id=operation_id,
        occurred_at=occurred_at,
        ticket_id=ticket.ticket_id.value,
        state=ticket.state.value,
        queue=None if queue is None else queue.number,
        lock_held=lock_held,
        error_code=error_code,
        message=message,
    )
