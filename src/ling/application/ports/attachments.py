"""Attachment credentials and sessions. These records are not domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SlotCredential:
    """SHA-256 hex digest that proves a later attach. The raw token is not stored."""

    slot_id: str
    token_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AttachmentSession:
    """One short-lived attachment. `revoked_at` is set when the session is closed."""

    session_id: str
    slot_id: str
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None

    def revoke(self, at: datetime) -> AttachmentSession:
        """Return this session marked revoked at `at`. `at` must carry a timezone."""

        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError("revocation time must be timezone-aware")
        return AttachmentSession(
            session_id=self.session_id,
            slot_id=self.slot_id,
            expires_at=self.expires_at,
            created_at=self.created_at,
            revoked_at=at,
        )


class SlotCredentialRepository(Protocol):
    """Load and stage one credential per slot."""

    def get(self, slot_id: str) -> SlotCredential | None:
        """Return the staged or stored credential, or None."""

    def save(self, credential: SlotCredential) -> None:
        """Stage `credential` until the unit of work commits."""


class AttachmentSessionRepository(Protocol):
    """Load and stage attachment sessions."""

    def get(self, session_id: str) -> AttachmentSession | None:
        """Return the staged or stored session, or None."""

    def save(self, session: AttachmentSession) -> None:
        """Stage `session` until the unit of work commits."""
