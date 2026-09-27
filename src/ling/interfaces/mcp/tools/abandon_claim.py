"""MCP adapter for abandoning a claimed ticket."""

from typing import Any

from ling.application.commands.abandon_claim import execute
from ling.application.dto import AbandonClaimCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, require_text, validated
from ling.interfaces.mcp.schemas import AbandonClaimInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and abandon one claimed ticket."""

    parsed, error = validated(AbandonClaimInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, AbandonClaimInput)
    actor_slot_id, missing = require_text(parsed.actor_slot_id, deps, "actor_slot_id")
    if missing is not None or actor_slot_id is None:
        return missing or {}
    return call_use_case(
        deps,
        execute,
        AbandonClaimCommand(
            actor_slot_id=actor_slot_id,
            ticket_id=parsed.ticket_id,
            operation_id=parsed.operation_id,
        ),
    )
