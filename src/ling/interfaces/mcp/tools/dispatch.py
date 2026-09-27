"""MCP adapter for mentor dispatch."""

from typing import Any

from ling.application.commands.dispatch import execute
from ling.application.dto import DispatchCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, require_text, validated
from ling.interfaces.mcp.schemas import DispatchInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and dispatch one ticket."""

    parsed, error = validated(DispatchInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, DispatchInput)
    issuer_slot_id, missing = require_text(parsed.issuer_slot_id, deps, "issuer_slot_id")
    if missing is not None or issuer_slot_id is None:
        return missing or {}
    return call_use_case(
        deps,
        execute,
        DispatchCommand(
            issuer_slot_id=issuer_slot_id,
            target_template_id=parsed.target_template_id,
            content=parsed.content,
            target_slot_id=parsed.target_slot_id,
            operation_id=parsed.operation_id,
        ),
    )
