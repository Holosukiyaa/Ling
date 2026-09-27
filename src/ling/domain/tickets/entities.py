"""Ticket aggregate. Status changes only through the closed state machine."""

from __future__ import annotations

from dataclasses import dataclass

from ling.domain.agents.values import SlotId
from ling.domain.errors import AlreadyClaimed, InvalidTransition, NotClaimant, NotIssuer, NotTarget
from ling.domain.queues import Queue, queue_for
from ling.domain.tickets.states import ReviewResult, TicketState
from ling.domain.tickets.transitions import TicketMachine


@dataclass(frozen=True, slots=True)
class TicketId:
    """Stable identity of one ticket. It does not change across states."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("ticket id must be a non-empty string")


class Ticket:
    """One unit of work from dispatch through mentor consumption.

    Queue membership is derived from state. There is no queue field to assign.
    `target_slot_id` is optional. When it is set, only that slot may claim.
    """

    def __init__(
        self,
        ticket_id: TicketId,
        issuer: SlotId,
        content: str,
        target_slot_id: SlotId | None = None,
    ) -> None:
        if not isinstance(content, str):
            raise ValueError("ticket content must be a string")
        if target_slot_id is not None and not isinstance(target_slot_id, SlotId):
            raise ValueError("target slot id must be a slot id")
        self.ticket_id = ticket_id
        self.issuer = issuer
        self.content = content
        self.target_slot_id = target_slot_id
        self.claimant: SlotId | None = None
        self.review_result: ReviewResult | None = None
        self._machine = TicketMachine()

    @property
    def state(self) -> TicketState:
        return self._machine.state

    @property
    def queue(self) -> Queue | None:
        return queue_for(self.state)

    @property
    def auto_transitions(self) -> bool:
        """False when the transitions graph does not synthesize jump triggers."""

        return self._machine.auto_transitions

    def claim(self, actor: SlotId) -> None:
        """queued -> claimed, recording the single claimant."""

        if self.state is TicketState.CLAIMED or self.claimant is not None:
            raise AlreadyClaimed(f"{self.ticket_id.value} already has a claimant")
        if self.state is not TicketState.QUEUED:
            raise InvalidTransition(f"cannot claim from {self.state.value}")
        if self.target_slot_id is not None and actor != self.target_slot_id:
            raise NotTarget(
                f"{actor.value} cannot claim {self.ticket_id.value} "
                f"targeted at {self.target_slot_id.value}"
            )
        self._machine.fire("claim")
        self.claimant = actor

    def submit(self, actor: SlotId) -> None:
        """claimed -> submitted, only by the recorded claimant."""

        if self.state is not TicketState.CLAIMED:
            raise InvalidTransition(f"cannot submit from {self.state.value}")
        if actor != self.claimant:
            raise NotClaimant(f"{actor.value} is not the claimant of {self.ticket_id.value}")
        self._machine.fire("submit")

    def accept(self) -> None:
        """submitted -> accepted. This is a checker outcome on queue 2."""

        self._review("accept", ReviewResult.ACCEPTED)

    def reject(self) -> None:
        """submitted -> rejected. A legal checker rejection, not a refused call."""

        self._review("reject", ReviewResult.REJECTED)

    def consume(self, actor: SlotId) -> None:
        """accepted|rejected -> consumed, only by the issuing slot."""

        if actor != self.issuer:
            raise NotIssuer(f"{actor.value} did not issue {self.ticket_id.value}")
        if self.state not in (TicketState.ACCEPTED, TicketState.REJECTED):
            raise InvalidTransition(f"cannot consume from {self.state.value}")
        self._machine.fire("consume")

    def abandon(self, actor: SlotId) -> None:
        """claimed -> queued, only by the recorded claimant. The claimant is cleared."""

        if self.state is not TicketState.CLAIMED:
            raise InvalidTransition(f"cannot abandon from {self.state.value}")
        if actor != self.claimant:
            raise NotClaimant(f"{actor.value} is not the claimant of {self.ticket_id.value}")
        self._machine.fire("abandon")
        self.claimant = None

    def fire(self, event: str) -> None:
        """Run a named event. Unknown names fail and leave the ticket untouched.

        Prefer claim/submit/accept/reject/consume/abandon. This entry exists so an
        unrecognized trigger is rejected without using a transitions exception.
        """

        before = self._snapshot()
        try:
            if event == "claim":
                raise InvalidTransition("claim requires a claimant slot")
            if event == "submit":
                raise InvalidTransition("submit requires the claimant slot")
            if event == "accept":
                self.accept()
            elif event == "reject":
                self.reject()
            elif event == "consume":
                raise InvalidTransition("consume requires the issuer slot")
            elif event == "abandon":
                raise InvalidTransition("abandon requires the claimant slot")
            else:
                self._machine.fire(event)
        except Exception:
            self._restore(before)
            raise

    def _review(self, event: str, result: ReviewResult) -> None:
        if self.state is not TicketState.SUBMITTED:
            raise InvalidTransition(f"cannot {event} from {self.state.value}")
        self._machine.fire(event)
        self.review_result = result

    def _snapshot(self) -> tuple[str, SlotId | None, ReviewResult | None]:
        return (self.state.value, self.claimant, self.review_result)

    def _restore(self, snapshot: tuple[str, SlotId | None, ReviewResult | None]) -> None:
        _status, claimant, review_result = snapshot
        self.claimant = claimant
        self.review_result = review_result
