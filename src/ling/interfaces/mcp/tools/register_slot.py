"""MCP adapter for local slot registration."""

from typing import Any

from ling.application.commands.register_slot import execute
from ling.application.dto import RegisterSlotCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import RegisterSlotInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and register one local slot."""

    parsed, error = validated(RegisterSlotInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, RegisterSlotInput)
    return call_use_case(
        deps,
        execute,
        RegisterSlotCommand(slot_id=parsed.slot_id, template_id=parsed.template_id),
    )
