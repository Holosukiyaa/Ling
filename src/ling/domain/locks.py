"""Consumption lock: one issuing mentor, one unconsumed ticket."""

from __future__ import annotations

from ling.domain.agents.values import SlotId
from ling.domain.errors import ConsumptionLockHeld, InvalidTransition, LockMismatch, NotIssuer
from ling.domain.tickets.entities import Ticket, TicketId
from ling.domain.tickets.states import TicketState


class ConsumptionLock:
    """Stops a mentor from occupying another dispatch while a result is open.

    Checker rejection does not release the lock. Only consuming that ticket does.
    This object is Ling's record. It is not an Agent Coordinator file lock.
    """

    def __init__(self, mentor: SlotId) -> None:
        self.mentor = mentor
        self.ticket_id: TicketId | None = None

    @property
    def held(self) -> bool:
        return self.ticket_id is not None

    def occupy(self, ticket_id: TicketId) -> None:
        """Reserve the mentor's dispatch slot for `ticket_id`."""

        if self.ticket_id is not None:
            raise ConsumptionLockHeld(
                f"{self.mentor.value} still holds {self.ticket_id.value}"
            )
        self.ticket_id = ticket_id

    def consume(self, ticket: Ticket, actor: SlotId) -> None:
        """Consume `ticket` and release. A refused call leaves the lock held."""

        if actor != self.mentor or ticket.issuer != self.mentor:
            raise NotIssuer(f"{actor.value} cannot consume for {self.mentor.value}")
        if self.ticket_id != ticket.ticket_id:
            raise LockMismatch(
                f"{self.mentor.value} holds {self.ticket_id.value if self.ticket_id else 'nothing'}"
            )
        if ticket.state not in (TicketState.ACCEPTED, TicketState.REJECTED):
            raise InvalidTransition(
                f"cannot release a consumption lock from {ticket.state.value}"
            )
        ticket.consume(actor)
        self.ticket_id = None
