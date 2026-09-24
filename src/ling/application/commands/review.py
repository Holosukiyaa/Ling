"""Checker review. Rejection is a legal result and leaves the consumption lock held."""

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
from ling.application.dto import FORBIDDEN, INVALID_INPUT, ReviewCommand, ReviewResult
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import CHECKER_ID
from ling.domain.errors import DomainError
from ling.domain.tickets.entities import Ticket


def execute(
    command: ReviewCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> ReviewResult:
    """Accept or reject a submitted ticket. The consumption lock is not saved."""

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
    if command.decision not in ("accept", "reject"):
        uow.rollback()
        return _plain(
            operation_id,
            occurred_at,
            INVALID_INPUT,
            "decision must be accept or reject",
            ticket_id=ticket_id.value,
        )
    actor = load_slot(uow, actor_id)
    if actor is None:
        uow.rollback()
        code, message = not_found(f"slot {actor_id.value} is not registered")
        return _plain(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    if actor.template.template_id != CHECKER_ID:
        uow.rollback()
        return _plain(
            operation_id,
            occurred_at,
            FORBIDDEN,
            f"{actor.template.template_id.value} cannot review",
            ticket_id=ticket_id.value,
        )
    ticket = load_ticket(uow, ticket_id)
    if ticket is None:
        uow.rollback()
        code, message = not_found(f"ticket {ticket_id.value} does not exist")
        return _plain(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    try:
        if command.decision == "accept":
            ticket.accept()
        else:
            ticket.reject()
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
    return _snapshot(operation_id, occurred_at, ticket, ok=True, message=command.decision)


def _plain(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
    *,
    ticket_id: str | None,
) -> ReviewResult:
    return ReviewResult(
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
) -> ReviewResult:
    queue = ticket.queue
    review = ticket.review_result
    return ReviewResult(
        ok=ok,
        operation_id=operation_id,
        occurred_at=occurred_at,
        ticket_id=ticket.ticket_id.value,
        state=ticket.state.value,
        queue=None if queue is None else queue.number,
        review_result=None if review is None else review.value,
        error_code=error_code,
        message=message,
    )
