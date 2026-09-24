"""Ask the coordinator for a file lock. This command does not store a lease."""

from __future__ import annotations

from datetime import datetime

from ling.application.commands.support import (
    call_coordinator,
    coordinator_code,
    load_slot,
    load_ticket,
    not_found,
    parse_agent_id,
    parse_slot_id,
    parse_ticket_id,
)
from ling.application.dto import (
    FORBIDDEN,
    INVALID_INPUT,
    AcquireFileLockCommand,
    AcquireFileLockResult,
)
from ling.application.ports.clock import Clock
from ling.application.ports.coordinator import CoordinatorPort
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import WORKER_ID
from ling.domain.tickets.entities import Ticket
from ling.domain.tickets.states import TicketState


def execute(
    command: AcquireFileLockCommand,
    *,
    uow: UnitOfWork,
    coordinator: CoordinatorPort,
    ids: IdGenerator,
    clock: Clock,
) -> AcquireFileLockResult:
    """Check the claimant, then call the coordinator. Ling state stays put."""

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
    try:
        agent_id = parse_agent_id(command.agent_id)
    except ValueError as exc:
        uow.rollback()
        return _plain(
            operation_id,
            occurred_at,
            INVALID_INPUT,
            str(exc),
            ticket_id=ticket_id.value,
        )
    if not command.paths or any(not isinstance(path, str) or not path.strip() for path in command.paths):
        uow.rollback()
        return _plain(
            operation_id,
            occurred_at,
            INVALID_INPUT,
            "at least one non-empty path is required",
            ticket_id=ticket_id.value,
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
            f"{actor.template.template_id.value} cannot acquire a file lock",
            ticket_id=ticket_id.value,
        )
    ticket = load_ticket(uow, ticket_id)
    if ticket is None:
        uow.rollback()
        code, message = not_found(f"ticket {ticket_id.value} does not exist")
        return _plain(operation_id, occurred_at, code, message, ticket_id=ticket_id.value)
    if ticket.state is not TicketState.CLAIMED or ticket.claimant != actor.slot_id:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=FORBIDDEN,
            message=f"{actor.slot_id.value} does not hold {ticket.ticket_id.value}",
        )
    external = call_coordinator(
        lambda: coordinator.acquire_file_lock(
            slot_id=actor.slot_id.value,
            agent_id=agent_id,
            ticket_id=ticket.ticket_id.value,
            operation_id=operation_id,
            paths=command.paths,
        )
    )
    if not external.ok:
        uow.rollback()
        return _snapshot(
            operation_id,
            occurred_at,
            ticket,
            ok=False,
            error_code=coordinator_code(external),
            message=external.message,
        )
    # Ling does not store the coordinator lease.
    uow.commit()
    return _snapshot(
        operation_id,
        occurred_at,
        ticket,
        ok=True,
        message=external.message,
    )


def _plain(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
    *,
    ticket_id: str | None,
) -> AcquireFileLockResult:
    return AcquireFileLockResult(
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
) -> AcquireFileLockResult:
    queue = ticket.queue
    claimant = ticket.claimant
    return AcquireFileLockResult(
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
