"""MCP adapter for claimant submit."""

from typing import Any

from ling.application.commands.submit import execute
from ling.application.dto import SubmitCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import SubmitInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and submit one claimed ticket."""

    parsed, error = validated(SubmitInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, SubmitInput)
    return call_use_case(
        deps,
        execute,
        SubmitCommand(
            actor_slot_id=parsed.actor_slot_id,
            ticket_id=parsed.ticket_id,
            operation_id=parsed.operation_id,
        ),
    )
