"""Consumption lock stays held through both checker outcomes until consume."""

from __future__ import annotations

import pytest

from ling.domain.agents import Slot, SlotId
from ling.domain.agents.entities import WORKER
from ling.domain.errors import ConsumptionLockHeld, InvalidTransition
from ling.domain.locks import ConsumptionLock
from ling.domain.queues import Queue
from ling.domain.tickets import Ticket, TicketId, TicketState


def _flow(decision: str) -> tuple[ConsumptionLock, Ticket, SlotId]:
    mentor_id = SlotId("mentor-1")
    worker = Slot(SlotId("worker-1"), WORKER)
    ticket = Ticket(TicketId("ticket-9"), mentor_id, "ship the change")
    lock = ConsumptionLock(mentor_id)
    lock.occupy(ticket.ticket_id)
    ticket.claim(worker.slot_id)
    ticket.submit(worker.slot_id)
    if decision == "accept":
        ticket.accept()
    else:
        ticket.reject()
    return lock, ticket, mentor_id


def test_accepted_result_releases_only_after_consume() -> None:
    lock, ticket, mentor_id = _flow("accept")
    assert ticket.queue is Queue.RESULTS
    assert lock.held is True
    assert lock.ticket_id == ticket.ticket_id

    with pytest.raises(ConsumptionLockHeld):
        lock.occupy(TicketId("ticket-10"))
    assert lock.ticket_id == ticket.ticket_id

    lock.consume(ticket, mentor_id)
    assert ticket.state is TicketState.CONSUMED
    assert lock.held is False
    assert lock.ticket_id is None
    lock.occupy(TicketId("ticket-10"))
    assert lock.ticket_id == TicketId("ticket-10")


def test_rejected_result_still_needs_consume() -> None:
    lock, ticket, mentor_id = _flow("reject")
    assert ticket.state is TicketState.REJECTED
    assert ticket.queue is Queue.RESULTS
    assert lock.held is True

    with pytest.raises(ConsumptionLockHeld):
        lock.occupy(TicketId("ticket-10"))
    assert lock.ticket_id == ticket.ticket_id
    assert ticket.state is TicketState.REJECTED

    lock.consume(ticket, mentor_id)
    assert ticket.state is TicketState.CONSUMED
    assert ticket.queue is None
    assert lock.held is False
    assert lock.ticket_id is None


def test_consume_before_a_result_does_not_release() -> None:
    mentor_id = SlotId("mentor-1")
    worker = Slot(SlotId("worker-1"), WORKER)
    ticket = Ticket(TicketId("ticket-3"), mentor_id, "wait")
    lock = ConsumptionLock(mentor_id)
    lock.occupy(ticket.ticket_id)
    ticket.claim(worker.slot_id)
    ticket.submit(worker.slot_id)

    with pytest.raises(InvalidTransition):
        lock.consume(ticket, mentor_id)
    assert ticket.state is TicketState.SUBMITTED
    assert ticket.queue is Queue.REVIEW
    assert ticket.claimant == worker.slot_id
    assert lock.held is True
    assert lock.ticket_id == ticket.ticket_id


def test_refused_lock_consume_leaves_ticket_and_queue() -> None:
    lock, ticket, mentor_id = _flow("reject")
    stranger = SlotId("mentor-2")
    before = (ticket.state, ticket.queue, ticket.claimant, ticket.review_result, lock.ticket_id)
    with pytest.raises(Exception) as caught:
        lock.consume(ticket, stranger)
    assert caught.value.__class__.__module__.startswith("ling.domain")
    assert (ticket.state, ticket.queue, ticket.claimant, ticket.review_result, lock.ticket_id) == before
    assert mentor_id == lock.mentor
