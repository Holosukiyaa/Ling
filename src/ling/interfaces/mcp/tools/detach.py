"""MCP adapter for revoking the current stdio session."""

from typing import Any

from ling.application.commands.detach import execute
from ling.application.dto import DetachCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import DetachInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Revoke the bound session and clear it. A second call still succeeds."""

    _parsed, error = validated(DetachInput, arguments, deps)
    if error is not None:
        return error
    result = call_use_case(
        deps,
        execute,
        DetachCommand(session_id=deps.attachment.session_id),
    )
    if result.get("ok"):
        deps.attachment.clear()
    return result
