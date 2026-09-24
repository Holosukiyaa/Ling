"""Three named queues. Membership is derived from ticket state."""

from __future__ import annotations

from enum import Enum

from ling.domain.tickets.states import TicketState


class Queue(Enum):
    """Queue 1 is tasks, queue 3 is pending review, queue 2 is results."""

    TASKS = 1
    RESULTS = 2
    REVIEW = 3

    @property
    def number(self) -> int:
        return int(self.value)


_QUEUE_BY_STATE: dict[TicketState, Queue | None] = {
    TicketState.QUEUED: Queue.TASKS,
    TicketState.CLAIMED: Queue.TASKS,
    TicketState.SUBMITTED: Queue.REVIEW,
    TicketState.ACCEPTED: Queue.RESULTS,
    TicketState.REJECTED: Queue.RESULTS,
    TicketState.CONSUMED: None,
}


def queue_for(state: TicketState) -> Queue | None:
    """Return the queue implied by `state`, or None once the ticket is consumed."""

    try:
        return _QUEUE_BY_STATE[state]
    except KeyError as exc:
        raise ValueError(f"unknown ticket state: {state!r}") from exc
