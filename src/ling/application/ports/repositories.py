"""Repository protocols. Infrastructure supplies the storage; application only sees these."""

from __future__ import annotations

from typing import Protocol

from ling.domain.agents.values import SlotId
from ling.domain.agents.entities import Slot
from ling.domain.locks import ConsumptionLock
from ling.domain.tickets.entities import Ticket, TicketId


class SlotRepository(Protocol):
    """Load and stage slots. `find` is the same lookup as `get`."""

    def get(self, slot_id: SlotId) -> Slot | None:
        """Return the slot staged in this unit of work, or None."""

    def find(self, slot_id: SlotId) -> Slot | None:
        """Alias of `get` for callers that speak in finder terms."""

    def save(self, slot: Slot) -> None:
        """Stage `slot` until the unit of work commits."""


class TicketRepository(Protocol):
    """Load and stage tickets inside one unit of work."""

    def get(self, ticket_id: TicketId) -> Ticket | None:
        """Return the ticket staged in this unit of work, or None."""

    def find(self, ticket_id: TicketId) -> Ticket | None:
        """Alias of `get`."""

    def save(self, ticket: Ticket) -> None:
        """Stage `ticket` until the unit of work commits."""


class ConsumptionLockRepository(Protocol):
    """Load and stage one consumption lock per mentor slot."""

    def get(self, mentor: SlotId) -> ConsumptionLock | None:
        """Return the mentor's lock staged in this unit of work, or None."""

    def find(self, mentor: SlotId) -> ConsumptionLock | None:
        """Alias of `get`."""

    def save(self, lock: ConsumptionLock) -> None:
        """Stage `lock` until the unit of work commits."""
