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
from ling.application.ports.unit_of_work import UnitOfWork


def execute(
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> DashboardResult:
    """Collect the current local records. This query does not change them."""

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
