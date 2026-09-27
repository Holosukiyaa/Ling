"""MCP adapter for checker review."""

from typing import Any

from ling.application.commands.review import execute
from ling.application.dto import ReviewCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import ReviewInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and record one review decision."""

    parsed, error = validated(ReviewInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, ReviewInput)
    return call_use_case(
        deps,
        execute,
        ReviewCommand(
            actor_slot_id=parsed.actor_slot_id,
            ticket_id=parsed.ticket_id,
            decision=parsed.decision,
            operation_id=parsed.operation_id,
        ),
    )
