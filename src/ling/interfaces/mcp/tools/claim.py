"""MCP adapter for a local ticket claim."""

from typing import Any

from ling.application.commands.claim import execute
from ling.application.dto import ClaimCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, require_text, validated
from ling.interfaces.mcp.schemas import ClaimInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and claim one queued ticket."""

    parsed, error = validated(ClaimInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, ClaimInput)
    actor_slot_id, missing = require_text(parsed.actor_slot_id, deps, "actor_slot_id")
    if missing is not None or actor_slot_id is None:
        return missing or {}
    return call_use_case(
        deps,
        execute,
        ClaimCommand(
            actor_slot_id=actor_slot_id,
            ticket_id=parsed.ticket_id,
            operation_id=parsed.operation_id,
        ),
    )
