"""Controller lease records. These are not domain entities and not file locks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class ControllerLeaseHeld(Exception):
    """Another connection published the single active lease first."""

    def __init__(self, lease_id: str) -> None:
        super().__init__(lease_id)
        self.lease_id = lease_id


@dataclass(frozen=True, slots=True)
class ControllerLease:
    """One commander lease bound to an attachment session.

    `active` is the single-holder flag. Releasing clears it. The raw attachment
    token is not part of this record.
    """

    lease_id: str
    slot_id: str
    session_id: str
    acquired_at: datetime
    expires_at: datetime
    released_at: datetime | None = None
    active: bool = True

    def release(self, at: datetime) -> ControllerLease:
        """Return this lease inactive at `at`. `at` must carry a timezone."""

        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError("release time must be timezone-aware")
        return ControllerLease(
            lease_id=self.lease_id,
            slot_id=self.slot_id,
            session_id=self.session_id,
            acquired_at=self.acquired_at,
            expires_at=self.expires_at,
            released_at=at,
            active=False,
        )

    def renew(self, expires_at: datetime) -> ControllerLease:
        """Return this same lease with a later expiry. The holder does not change."""

        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
            raise ValueError("expiry must be timezone-aware")
        return ControllerLease(
            lease_id=self.lease_id,
            slot_id=self.slot_id,
            session_id=self.session_id,
            acquired_at=self.acquired_at,
            expires_at=expires_at,
            released_at=None,
            active=True,
        )


class ControllerLeaseRepository(Protocol):
    """Load and stage controller leases inside the current unit of work."""

    def get(self, lease_id: str) -> ControllerLease | None:
        """Return one lease by id, or None."""

    def get_active(self) -> ControllerLease | None:
        """Return the staged or stored active lease, or None."""

    def save(self, lease: ControllerLease) -> None:
        """Stage `lease` until the unit of work commits."""
