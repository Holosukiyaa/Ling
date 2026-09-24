"""Optional observation of a finished MCP call. Plain mappings only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class RuntimeObserver(Protocol):
    """See one finished tool call. Implementations must not affect the caller."""

    def observe(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        """Receive the tool name, its arguments, and the local result."""


class NullRuntimeObserver:
    """Observation that makes no external request."""

    def observe(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        """Ignore the call."""
