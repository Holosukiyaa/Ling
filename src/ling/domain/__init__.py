"""Domain layer. Rules and types only; no outer-layer imports."""

from ling.domain.errors import (
    AlreadyClaimed,
    ConsumptionLockHeld,
    DomainError,
    InvalidTransition,
    LockMismatch,
    NotClaimant,
    NotIssuer,
    UnknownEvent,
)

__all__ = [
    "AlreadyClaimed",
    "ConsumptionLockHeld",
    "DomainError",
    "InvalidTransition",
    "LockMismatch",
    "NotClaimant",
    "NotIssuer",
    "UnknownEvent",
]
