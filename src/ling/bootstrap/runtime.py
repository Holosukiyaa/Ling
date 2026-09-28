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

    def new_lease_id(self) -> str:
        return uuid4().hex


DEFAULT_ATTACHMENT_TTL_SECONDS = 3600
DEFAULT_CONTROLLER_LEASE_TTL_SECONDS = 3600


def attachment_ttl_seconds(raw: object) -> int:
    """Return a positive attachment TTL in seconds. Blank or illegal values use the default."""

    parsed = _positive_int(raw)
    if parsed is None:
        return DEFAULT_ATTACHMENT_TTL_SECONDS
    return parsed


def attachment_ttl_rejected(raw: object) -> bool:
    """True when a non-empty setting is not a positive integer."""

    return _rejected_positive(raw)


def controller_lease_ttl_seconds(raw: object) -> int:
    """Return a positive controller-lease TTL. Blank or illegal values use the default."""

    parsed = _positive_int(raw)
    if parsed is None:
        return DEFAULT_CONTROLLER_LEASE_TTL_SECONDS
    return parsed


def controller_lease_ttl_rejected(raw: object) -> bool:
    """True when a non-empty controller-lease TTL is not a positive integer."""

    return _rejected_positive(raw)


def _rejected_positive(raw: object) -> bool:
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


DEFAULT_HEARTBEAT_STALE_SECONDS = 900
DEFAULT_PROGRESS_STALE_SECONDS = 1800
DEFAULT_REQUEST_TIMEOUT_SECONDS = 1800
DEFAULT_INSPECT_INTERVAL_SECONDS = 5.0
DEFAULT_PING_INTERVAL_SECONDS = 30.0
DEFAULT_PING_TIMEOUT_SECONDS = 2.0
DEFAULT_QUEUE_POLL_SECONDS = 1.0


def heartbeat_stale_seconds(raw: object) -> int:
    """Return the presence threshold. Blank or illegal values use 900 seconds."""

    parsed = _positive_int(raw)
    if parsed is None:
        return DEFAULT_HEARTBEAT_STALE_SECONDS
    return parsed


def progress_stale_seconds(raw: object) -> int:
    """Return the ticket no-progress threshold. Blank or illegal values use 1800."""

    parsed = _positive_int(raw)
    if parsed is None:
        return DEFAULT_PROGRESS_STALE_SECONDS
    return parsed


def request_timeout_seconds(raw: object) -> int:
    """Return the unfinished-request threshold. Blank or illegal values use 1800."""

    parsed = _positive_int(raw)
    if parsed is None:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    return parsed


def positive_seconds(raw: object, default: float) -> float:
    """Return a positive number of seconds, or `default` when the value is illegal."""

    if raw is None:
        return default
    text = str(raw).strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def positive_seconds_rejected(raw: object) -> bool:
    """True when a non-empty setting is not a positive number."""

    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    return positive_seconds(raw, -1) <= 0


def new_instance_id() -> str:
    """Return a runtime instance id. It is not a session id or a credential."""

    return uuid4().hex
