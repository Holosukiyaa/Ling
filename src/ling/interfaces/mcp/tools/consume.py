"""MCP adapter for mentor consumption."""

from typing import Any

from ling.application.commands.consume import execute
from ling.application.dto import ConsumeCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import ConsumeInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and consume one reviewed ticket."""

    parsed, error = validated(ConsumeInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, ConsumeInput)
    return call_use_case(
        deps,
        execute,
        ConsumeCommand(actor_slot_id=parsed.actor_slot_id, ticket_id=parsed.ticket_id),
    )
