"""Clock port. Application reads time through this protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current instant. Standard library `datetime` only."""

    def now(self) -> datetime:
        """Return the current time."""
