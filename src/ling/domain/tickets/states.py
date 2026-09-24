"""Ticket lifecycle states. The set is closed."""

from __future__ import annotations

from enum import StrEnum


class TicketState(StrEnum):
    """Legal ticket states. Anything else is not a ticket state."""

    QUEUED = "queued"
    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CONSUMED = "consumed"


class ReviewResult(StrEnum):
    """Checker outcome recorded when a submission enters the result queue."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
