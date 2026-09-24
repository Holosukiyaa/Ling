"""Ticket lifecycle, queue derivation, and refused calls."""

from __future__ import annotations

import pytest

from ling.domain.agents import Slot, SlotId
from ling.domain.agents.entities import MENTOR, WORKER, Template
from ling.domain.errors import (
    AlreadyClaimed,
    DomainError,
    InvalidTransition,
    NotClaimant,
    UnknownEvent,
)
from ling.domain.queues import Queue
from ling.domain.tickets import ReviewResult, Ticket, TicketId, TicketState


def _slot(name: str, template: Template) -> Slot:
    return Slot(slot_id=SlotId(name), template=template)


def _ticket() -> tuple[Ticket, Slot, Slot, Slot]:
    mentor = _slot("mentor-1", MENTOR)
    worker = _slot("worker-1", WORKER)
    other = _slot("worker-2", WORKER)
    ticket = Ticket(TicketId("ticket-1"), mentor.slot_id, "implement the slice")
    return ticket, mentor, worker, other


def _snapshot(ticket: Ticket) -> tuple[object, ...]:
    return (ticket.state, ticket.queue, ticket.claimant, ticket.review_result, ticket.content)


def test_success_path_maps_each_state_to_its_queue() -> None:
    ticket, mentor, worker, _other = _ticket()
    assert ticket.state is TicketState.QUEUED
    assert ticket.queue is Queue.TASKS
    assert ticket.queue is not None and ticket.queue.number == 1

    ticket.claim(worker.slot_id)
    assert ticket.state is TicketState.CLAIMED
    assert ticket.queue is Queue.TASKS
    assert ticket.claimant == worker.slot_id

    ticket.submit(worker.slot_id)
    assert ticket.state is TicketState.SUBMITTED
    assert ticket.queue is Queue.REVIEW
    assert ticket.queue is not None and ticket.queue.number == 3

    ticket.accept()
    assert ticket.state is TicketState.ACCEPTED
    assert ticket.review_result is ReviewResult.ACCEPTED
    assert ticket.queue is Queue.RESULTS
    assert ticket.queue is not None and ticket.queue.number == 2

    ticket.consume(mentor.slot_id)
    assert ticket.state is TicketState.CONSUMED
    assert ticket.queue is None
    assert ticket.claimant == worker.slot_id
    assert ticket.review_result is ReviewResult.ACCEPTED


def test_legal_rejection_lands_on_result_queue() -> None:
    ticket, _mentor, worker, _other = _ticket()
    ticket.claim(worker.slot_id)
    ticket.submit(worker.slot_id)
    ticket.reject()
    assert ticket.state is TicketState.REJECTED
    assert ticket.review_result is ReviewResult.REJECTED
    assert ticket.queue is Queue.RESULTS


def test_skip_ahead_keeps_the_original_state() -> None:
    ticket, mentor, worker, _other = _ticket()
    before = _snapshot(ticket)
    with pytest.raises(InvalidTransition) as caught:
        ticket.submit(worker.slot_id)
    assert _snapshot(ticket) == before
    assert isinstance(caught.value, DomainError)

    ticket.claim(worker.slot_id)
    claimed = _snapshot(ticket)
    with pytest.raises(InvalidTransition):
        ticket.accept()
    with pytest.raises(InvalidTransition):
        ticket.consume(mentor.slot_id)
    assert _snapshot(ticket) == claimed

    ticket.submit(worker.slot_id)
    submitted = _snapshot(ticket)
    with pytest.raises(AlreadyClaimed):
        ticket.claim(worker.slot_id)
    with pytest.raises(InvalidTransition):
        ticket.consume(mentor.slot_id)
    assert _snapshot(ticket) == submitted


def test_second_worker_cannot_claim() -> None:
    ticket, _mentor, worker, other = _ticket()
    ticket.claim(worker.slot_id)
    before = _snapshot(ticket)
    with pytest.raises(AlreadyClaimed):
        ticket.claim(other.slot_id)
    assert _snapshot(ticket) == before


def test_non_claimant_cannot_submit() -> None:
    ticket, _mentor, worker, other = _ticket()
    ticket.claim(worker.slot_id)
    before = _snapshot(ticket)
    with pytest.raises(NotClaimant):
        ticket.submit(other.slot_id)
    assert _snapshot(ticket) == before
    assert ticket.state is TicketState.CLAIMED
    assert ticket.queue is Queue.TASKS


def test_refused_reject_does_not_change_ticket_or_queue() -> None:
    ticket, _mentor, worker, _other = _ticket()
    ticket.claim(worker.slot_id)
    before = _snapshot(ticket)
    with pytest.raises(InvalidTransition):
        ticket.reject()
    assert _snapshot(ticket) == before

    ticket.submit(worker.slot_id)
    ticket.reject()
    decided = _snapshot(ticket)
    with pytest.raises(InvalidTransition):
        ticket.reject()
    assert _snapshot(ticket) == decided
    assert ticket.queue is Queue.RESULTS


def test_auto_transitions_are_off_and_unknown_events_fail() -> None:
    ticket, _mentor, _worker, _other = _ticket()
    assert ticket.auto_transitions is False
    before = _snapshot(ticket)
    with pytest.raises(UnknownEvent) as caught:
        ticket.fire("to_consumed")
    assert caught.value.code == "unknown_event"
    assert type(caught.value).__module__.startswith("ling.domain")
    assert _snapshot(ticket) == before

    with pytest.raises(UnknownEvent):
        ticket.fire("jump")
    assert _snapshot(ticket) == before


def test_queue_has_no_assignable_field() -> None:
    ticket, _mentor, _worker, _other = _ticket()
    with pytest.raises(AttributeError):
        ticket.queue = Queue.RESULTS  # type: ignore[misc]
    assert ticket.queue is Queue.TASKS
