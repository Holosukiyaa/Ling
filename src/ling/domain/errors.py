"""Domain errors. Callers outside the domain depend on these types only."""

from __future__ import annotations


class DomainError(Exception):
    """Base error for a refused domain operation. The subject is left unchanged."""

    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidTransition(DomainError):
    """The requested status edge is not part of the ticket lifecycle."""

    code = "invalid_transition"


class AlreadyClaimed(DomainError):
    """A second claimant tried to take a ticket that already has a claimant."""

    code = "already_claimed"


class NotClaimant(DomainError):
    """A slot that does not hold the claim tried to act as the claimant."""

    code = "not_claimant"


class NotIssuer(DomainError):
    """Consume was attempted by a slot that did not issue the ticket."""

    code = "not_issuer"


class UnknownEvent(DomainError):
    """The event name is not a declared ticket trigger."""

    code = "unknown_event"


class ConsumptionLockHeld(DomainError):
    """The mentor still holds an unconsumed ticket and cannot occupy another."""

    code = "consumption_lock_held"


class LockMismatch(DomainError):
    """The lock is held for a different ticket than the one being released."""

    code = "lock_mismatch"


class FilePathHeld(DomainError):
    """A requested path is already recorded for another file lock."""

    code = "conflict"
