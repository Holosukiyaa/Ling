"""MCP adapter for a local slot heartbeat."""

from typing import Any

from ling.application.commands.heartbeat import execute
from ling.application.dto import HeartbeatCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import HeartbeatInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and record one local heartbeat."""

    parsed, error = validated(HeartbeatInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, HeartbeatInput)
    return call_use_case(
        deps,
        execute,
        HeartbeatCommand(slot_id=parsed.slot_id, operation_id=parsed.operation_id),
    )
