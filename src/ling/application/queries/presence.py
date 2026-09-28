"""Derive slot presence from the stored heartbeat time. This does not write."""

from __future__ import annotations

from datetime import datetime

DEFAULT_HEARTBEAT_STALE_SECONDS = 900


def classify_presence(
    last_heartbeat_at: datetime | None,
    now: datetime,
    stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
) -> tuple[bool, str, int | None]:
    """Return online, presence, and age. Missing or naive times are unknown.

    `online` is true only for a timezone-aware heartbeat that is not older
    than `stale_seconds`. A stored flag is ignored. Age is whole seconds.
    """

    limit = stale_seconds if isinstance(stale_seconds, int) and stale_seconds > 0 else DEFAULT_HEARTBEAT_STALE_SECONDS
    if (
        not isinstance(last_heartbeat_at, datetime)
        or last_heartbeat_at.tzinfo is None
        or not isinstance(now, datetime)
        or now.tzinfo is None
    ):
        return False, "unknown", None
    age = int((now - last_heartbeat_at).total_seconds())
    if age < 0:
        age = 0
    if age > limit:
        return False, "stale", age
    return True, "online", age
