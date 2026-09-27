"""Identifier port. Ticket ids and operation ids are allocated here."""

from __future__ import annotations

from typing import Protocol


class IdGenerator(Protocol):
    """Allocate identifiers. Implementations may be sequential or random."""

    def new_ticket_id(self) -> str:
        """Return a new non-empty ticket id."""

    def new_operation_id(self) -> str:
        """Return a new non-empty operation id used as an idempotency key."""

    def new_session_id(self) -> str:
        """Return a new opaque attachment session id."""
