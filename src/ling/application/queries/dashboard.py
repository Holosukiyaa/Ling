"""Read Ling's own slots, tickets, queues, locks, and heartbeats."""

from __future__ import annotations

from ling.application.dto import (
    DashboardConsumptionLock,
    DashboardFileLock,
    DashboardResult,
    DashboardSlot,
    DashboardTicket,
)
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import ReadOnlyUnitOfWork
from ling.domain.agents.entities import WORKER_ID
from ling.domain.agents.values import SlotId


def execute(
    *,
    uow: ReadOnlyUnitOfWork,
    ids: IdGenerator,
    clock: Clock,
    viewer_slot_id: str | None = None,
) -> DashboardResult:
    """Collect one committed snapshot. This query does not change rows or receipts.

    A worker viewer receives only its own slot and tickets targeted at that
    slot. Mentor and checker viewers, and an omitted viewer, receive the full
    snapshot. The viewer id comes from the attached session, not from a
    request field.
    """

    slots = tuple(
        DashboardSlot(
            slot_id=slot.slot_id.value,
            template_id=slot.template.template_id.value,
            online=slot.online,
            last_heartbeat_at=slot.last_heartbeat_at,
        )
        for slot in uow.slots.list()
    )
    tickets = tuple(
        DashboardTicket(
            ticket_id=ticket.ticket_id.value,
            issuer_slot_id=ticket.issuer.value,
            content=ticket.content,
            state=ticket.state.value,
            queue=None if ticket.queue is None else ticket.queue.number,
            claimant=None if ticket.claimant is None else ticket.claimant.value,
            review_result=None if ticket.review_result is None else ticket.review_result.value,
            target_slot_id=None if ticket.target_slot_id is None else ticket.target_slot_id.value,
        )
        for ticket in uow.tickets.list()
    )
    consumption_locks = tuple(
        DashboardConsumptionLock(
            mentor_slot_id=lock.mentor.value,
            ticket_id=None if lock.ticket_id is None else lock.ticket_id.value,
            held=lock.held,
        )
        for lock in uow.consumption_locks.list()
    )
    file_locks = tuple(
        sorted(
            (
                DashboardFileLock(
                    ticket_id=lock.ticket_id.value,
                    holder_slot_id=lock.holder.value,
                    paths=tuple(sorted(lock.paths)),
                )
                for lock in uow.file_locks.held()
            ),
            key=lambda item: item.ticket_id,
        )
    )
    if _is_worker_view(uow, viewer_slot_id):
        slots, tickets, consumption_locks, file_locks = _project_worker(
            slots,
            tickets,
            consumption_locks,
            file_locks,
            viewer_slot_id or "",
        )
    return DashboardResult(
        ok=True,
        operation_id=ids.new_operation_id(),
        occurred_at=clock.now(),
        message="dashboard",
        slots=slots,
        tickets=tickets,
        consumption_locks=consumption_locks,
        file_locks=file_locks,
    )


def _is_worker_view(uow: ReadOnlyUnitOfWork, viewer_slot_id: str | None) -> bool:
    """True when the session slot is a worker, or the viewer id is unusable.

    An unknown viewer fails closed to the empty worker projection. Omitting
    the viewer keeps the full snapshot for non-session callers.
    """

    if viewer_slot_id is None:
        return False
    try:
        slot_id = SlotId(viewer_slot_id)
    except ValueError:
        return True
    slot = uow.slots.get(slot_id)
    if slot is None:
        return True
    return slot.template.template_id == WORKER_ID


def _project_worker(
    slots: tuple[DashboardSlot, ...],
    tickets: tuple[DashboardTicket, ...],
    consumption_locks: tuple[DashboardConsumptionLock, ...],
    file_locks: tuple[DashboardFileLock, ...],
    viewer_slot_id: str,
) -> tuple[
    tuple[DashboardSlot, ...],
    tuple[DashboardTicket, ...],
    tuple[DashboardConsumptionLock, ...],
    tuple[DashboardFileLock, ...],
]:
    """Keep the viewer's slot and only tickets targeted at that exact slot.

    Order stays the full-snapshot order. Untargeted tickets, other workers'
    tickets, other claimants, other consumption locks, and file locks held
    for another slot are omitted entirely.
    """

    visible = tuple(ticket for ticket in tickets if ticket.target_slot_id == viewer_slot_id)
    visible_ids = {ticket.ticket_id for ticket in visible}
    return (
        tuple(slot for slot in slots if slot.slot_id == viewer_slot_id),
        visible,
        tuple(
            lock
            for lock in consumption_locks
            if lock.ticket_id is not None and lock.ticket_id in visible_ids
        ),
        tuple(
            lock
            for lock in file_locks
            if lock.ticket_id in visible_ids and lock.holder_slot_id == viewer_slot_id
        ),
    )
