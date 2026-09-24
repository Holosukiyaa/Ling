"""Claim a queued ticket inside Ling's own transaction."""

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
from ling.application.dto import (
    ALREADY_CLAIMED,
    FORBIDDEN,
    INVALID_INPUT,
    INVALID_TRANSITION,
    ClaimCommand,
    ClaimResult,
)
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import WORKER_ID
from ling.domain.errors import DomainError
from ling.domain.tickets.entities import Ticket
from ling.domain.tickets.states import TicketState


def execute(
    command: ClaimCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> ClaimResult:
    """Check the worker and the queued ticket, then record the claimant."""

    operation_id = ids.new_operation_id()
    occurred_at = clock.now()
    actor_id = parse_slot_id(command.actor_slot_id)
    ticket_id = parse_ticket_id(command.ticket_id)
    if actor_id is None or ticket_id is None:
        uow.rollback()
        return ClaimResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            error_code=INVALID_INPUT,
            message="actor slot id and ticket id must be non-empty strings",
        )
    actor = load_slot(uow, actor_id)
    if actor is None:
        uow.rollback()
        code, message = not_found(f"slot {actor_id.value} is not registered")
        return _failed(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    if actor.template.template_id != WORKER_ID:
        uow.rollback()
        return _failed(
            operation_id,
            occurred_at,
            FORBIDDEN,
            f"{actor.template.template_id.value} cannot claim",
            ticket_id=ticket_id.value,
        )
    ticket = load_ticket(uow, ticket_id)
    if ticket is None:
        uow.rollback()
        code, message = not_found(f"ticket {ticket_id.value} does not exist")
        return _failed(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    if ticket.state is TicketState.CLAIMED or ticket.claimant is not None:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=ALREADY_CLAIMED,
            message=f"cannot claim from {ticket.state.value}",
        )
    if ticket.state is not TicketState.QUEUED:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=INVALID_TRANSITION,
            message=f"cannot claim from {ticket.state.value}",
        )
    try:
        ticket.claim(actor.slot_id)
    except DomainError as exc:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=domain_code(exc),
            message=exc.message,
        )
    uow.tickets.save(ticket)
    uow.commit()
    return _snapshot(operation_id, occurred_at, ticket, ok=True, message="claimed")


def _failed(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
    *,
    ticket_id: str | None,
) -> ClaimResult:
    return ClaimResult(
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
    ok: bool,
    message: str,
    error_code: str | None = None,
) -> ClaimResult:
    queue = ticket.queue
    claimant = ticket.claimant
    return ClaimResult(
        ok=ok,
        operation_id=operation_id,
        occurred_at=occurred_at,
        ticket_id=ticket.ticket_id.value,
        state=ticket.state.value,
        queue=None if queue is None else queue.number,
        claimant=None if claimant is None else claimant.value,
        error_code=error_code,
        message=message,
    )
