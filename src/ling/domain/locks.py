"""Ling lock records. These are not operating-system locks or coordinator leases."""

from __future__ import annotations

from collections.abc import Iterable

from ling.domain.agents.values import SlotId
from ling.domain.errors import (
    ConsumptionLockHeld,
    FilePathHeld,
    InvalidTransition,
    LockMismatch,
    NotIssuer,
)
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


class FileLock:
    """Paths one claimed slot may write for one ticket.

    The record stays until that ticket is consumed, or until the claimant
    abandons the claim. Abandon deletes the record in the same commit that
    returns the ticket to queued. Nothing releases it while the ticket stays
    claimed. It does not lock the operating system.
    """

    def __init__(self, ticket_id: TicketId, holder: SlotId, paths: frozenset[str]) -> None:
        if not isinstance(paths, frozenset) or not paths or any(not path.strip() for path in paths):
            raise ValueError("file lock requires at least one non-empty path")
        self.ticket_id = ticket_id
        self.holder = holder
        self.paths = paths

    def include(self, paths: frozenset[str]) -> FileLock:
        """Return this lock plus `paths`. The holder and ticket stay the same."""

        if not paths:
            raise ValueError("file lock requires at least one non-empty path")
        return FileLock(self.ticket_id, self.holder, self.paths | paths)


def ensure_paths_available(
    locks: Iterable[FileLock],
    ticket_id: TicketId,
    paths: frozenset[str],
) -> None:
    """Refuse when any path is already recorded on a different ticket's lock."""

    for lock in locks:
        if lock.ticket_id == ticket_id:
            continue
        overlap = lock.paths & paths
        if overlap:
            path = sorted(overlap)[0]
            raise FilePathHeld(f"path {path} is held by {lock.holder.value}")
