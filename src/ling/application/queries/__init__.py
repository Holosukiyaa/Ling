"""Read-side use cases."""

from ling.application.queries.dashboard import execute as dashboard
from ling.application.queries.resolve_attachment import execute as resolve_attachment

__all__ = ["dashboard", "resolve_attachment"]
