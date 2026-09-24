"""Stdlib clock and identifier implementations used by the composition root."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


class SystemClock:
    """UTC clock. The standard library is the only time source."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UuidIdGenerator:
    """Random ticket and operation ids. They are not agent identifiers."""

    def new_ticket_id(self) -> str:
        return uuid4().hex

    def new_operation_id(self) -> str:
        return uuid4().hex
