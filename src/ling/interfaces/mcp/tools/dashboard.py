"""MCP adapter for the read-only local dashboard."""

from typing import Any

from ling.application.queries.dashboard import execute
from ling.interfaces.mcp.adapter import ToolDeps, read_dashboard, validated
from ling.interfaces.mcp.schemas import DashboardInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and return Ling's own records."""

    _parsed, error = validated(DashboardInput, arguments, deps)
    if error is not None:
        return error
    return read_dashboard(deps, execute)
