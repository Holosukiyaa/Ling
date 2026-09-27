"""MCP adapter for binding the current stdio session to a slot."""

from typing import Any

from ling.application.attachments import hash_attachment_token
from ling.application.commands.attach import execute
from ling.application.dto import AttachCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import AttachInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Check the token hash, store a session, and bind this process to it."""

    parsed, error = validated(AttachInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, AttachInput)
    result = call_use_case(
        deps,
        execute,
        AttachCommand(
            slot_id=parsed.slot_id,
            token_hash=hash_attachment_token(parsed.attachment_token),
            ttl_seconds=deps.attachment_ttl_seconds,
            replace_session_id=deps.attachment.session_id,
        ),
    )
    if (
        result.get("ok")
        and isinstance(result.get("session_id"), str)
        and isinstance(result.get("slot_id"), str)
    ):
        deps.attachment.bind(result["session_id"], result["slot_id"])
    return result
