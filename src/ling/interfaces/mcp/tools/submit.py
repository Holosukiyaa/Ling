"""MCP adapter for claimant submit."""

from typing import Any

from ling.application.commands.submit import execute
from ling.application.dto import SubmitCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, require_text, validated
from ling.interfaces.mcp.schemas import SubmitInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and submit one claimed ticket."""

    parsed, error = validated(SubmitInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, SubmitInput)
    actor_slot_id, missing = require_text(parsed.actor_slot_id, deps, "actor_slot_id")
    if missing is not None or actor_slot_id is None:
        return missing or {}
    return call_use_case(
        deps,
        execute,
        SubmitCommand(
            actor_slot_id=actor_slot_id,
            ticket_id=parsed.ticket_id,
            operation_id=parsed.operation_id,
        ),
    )
