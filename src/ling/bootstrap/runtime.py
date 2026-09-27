"""Stdlib clock and identifier implementations used by the composition root."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


class SystemClock:
    """UTC clock. The standard library is the only time source."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UuidIdGenerator:
    """Random ticket, operation, and attachment session ids. They are not agent identifiers."""

    def new_ticket_id(self) -> str:
        return uuid4().hex

    def new_operation_id(self) -> str:
        return uuid4().hex

    def new_session_id(self) -> str:
        return uuid4().hex


DEFAULT_ATTACHMENT_TTL_SECONDS = 3600


def attachment_ttl_seconds(raw: object) -> int:
    """Return a positive attachment TTL in seconds. Blank or illegal values use the default."""

    parsed = _positive_int(raw)
    if parsed is None:
        return DEFAULT_ATTACHMENT_TTL_SECONDS
    return parsed


def attachment_ttl_rejected(raw: object) -> bool:
    """True when a non-empty setting is not a positive integer."""

    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    return _positive_int(raw) is None


def _positive_int(raw: object) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or not text.isdecimal():
        return None
    value = int(text)
    if value <= 0:
        return None
    return value
