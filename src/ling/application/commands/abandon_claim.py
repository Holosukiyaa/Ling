"""Abandon a claimed ticket and release that ticket's file lock together."""

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
    FORBIDDEN,
    INVALID_INPUT,
    INVALID_TRANSITION,
    AbandonClaimCommand,
    AbandonClaimResult,
)
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import WORKER_ID
from ling.domain.errors import DomainError, NotClaimant
from ling.domain.tickets.entities import Ticket
from ling.domain.tickets.states import TicketState


def execute(
    command: AbandonClaimCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> AbandonClaimResult:
    """Return the claimant's ticket to queue 1 and delete its file lock."""

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
    if actor.template.template_id != WORKER_ID:
        uow.rollback()
        return _plain(
            operation_id,
            occurred_at,
            FORBIDDEN,
            f"{actor.template.template_id.value} cannot abandon a claim",
            ticket_id=ticket_id.value,
        )
    ticket = load_ticket(uow, ticket_id)
    if ticket is None:
        uow.rollback()
        code, message = not_found(f"ticket {ticket_id.value} does not exist")
        return _plain(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    if ticket.state is not TicketState.CLAIMED:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=INVALID_TRANSITION,
            message=f"cannot abandon from {ticket.state.value}",
        )
    if ticket.claimant != actor.slot_id:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=FORBIDDEN,
            message=f"{actor.slot_id.value} is not the claimant of {ticket.ticket_id.value}",
        )
    try:
        ticket.abandon(actor.slot_id)
    except NotClaimant as exc:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=FORBIDDEN,
            message=exc.message,
        )
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
    uow.file_locks.release(ticket.ticket_id)
    uow.commit()
    return _snapshot(operation_id, occurred_at, ticket, ok=True, message="abandoned")


def _plain(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
    *,
    ticket_id: str | None,
) -> AbandonClaimResult:
    return AbandonClaimResult(
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
) -> AbandonClaimResult:
    queue = ticket.queue
    claimant = ticket.claimant
    return AbandonClaimResult(
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
