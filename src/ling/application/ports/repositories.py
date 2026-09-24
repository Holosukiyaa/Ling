"""Repository protocols. Infrastructure supplies the storage; application only sees these."""

from __future__ import annotations

from typing import Protocol

from ling.domain.agents.values import SlotId
from ling.domain.agents.entities import Slot
from ling.domain.locks import ConsumptionLock, FileLock
from ling.domain.tickets.entities import Ticket, TicketId


class SlotRepository(Protocol):
    """Load and stage slots. `find` is the same lookup as `get`."""

    def get(self, slot_id: SlotId) -> Slot | None:
        """Return the slot staged in this unit of work, or None."""

    def find(self, slot_id: SlotId) -> Slot | None:
        """Alias of `get` for callers that speak in finder terms."""

    def save(self, slot: Slot) -> None:
        """Stage `slot` until the unit of work commits."""

    def list(self) -> tuple[Slot, ...]:
        """Return every slot visible in this unit of work, ordered by slot id."""


class TicketRepository(Protocol):
    """Load and stage tickets inside one unit of work."""

    def get(self, ticket_id: TicketId) -> Ticket | None:
        """Return the ticket staged in this unit of work, or None."""

    def find(self, ticket_id: TicketId) -> Ticket | None:
        """Alias of `get`."""

    def save(self, ticket: Ticket) -> None:
        """Stage `ticket` until the unit of work commits."""

    def list(self) -> tuple[Ticket, ...]:
        """Return every ticket visible in this unit of work, ordered by ticket id."""


class ConsumptionLockRepository(Protocol):
    """Load and stage one consumption lock per mentor slot."""

    def get(self, mentor: SlotId) -> ConsumptionLock | None:
        """Return the mentor's lock staged in this unit of work, or None."""

    def find(self, mentor: SlotId) -> ConsumptionLock | None:
        """Alias of `get`."""

    def save(self, lock: ConsumptionLock) -> None:
        """Stage `lock` until the unit of work commits."""

    def list(self) -> tuple[ConsumptionLock, ...]:
        """Return every consumption lock visible in this unit of work."""


class FileLockRepository(Protocol):
    """Load and stage one file-lock record per ticket."""

    def get(self, ticket_id: TicketId) -> FileLock | None:
        """Return the ticket's file lock staged in this unit of work, or None."""

    def find(self, ticket_id: TicketId) -> FileLock | None:
        """Alias of `get`."""

    def held(self) -> tuple[FileLock, ...]:
        """Return every file lock visible in this unit of work."""

    def save(self, lock: FileLock) -> None:
        """Stage `lock` until the unit of work commits."""

    def release(self, ticket_id: TicketId) -> None:
        """Stage removal of the ticket's file lock. Commit publishes the removal."""
