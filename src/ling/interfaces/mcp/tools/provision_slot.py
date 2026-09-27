"""MCP adapter for administrator-controlled slot credentials."""

from typing import Any

from ling.application.attachments import hash_attachment_token
from ling.application.commands.provision_slot import execute
from ling.application.dto import ProvisionSlotCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, require_text, validated
from ling.interfaces.mcp.schemas import ProvisionSlotInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Hash the token and provision one slot for the lease-holding commander."""

    parsed, error = validated(ProvisionSlotInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, ProvisionSlotInput)
    actor, missing = require_text(parsed.actor_slot_id, deps, "actor_slot_id")
    if missing is not None or actor is None:
        return missing or {}
    session_id, missing_session = require_text(deps.attachment.session_id, deps, "session_id")
    if missing_session is not None or session_id is None:
        return missing_session or {}
    return call_use_case(
        deps,
        execute,
        ProvisionSlotCommand(
            actor_slot_id=actor,
            session_id=session_id,
            slot_id=parsed.slot_id,
            template_id=parsed.template_id,
            token_hash=hash_attachment_token(parsed.attachment_token),
            operation_id=parsed.operation_id,
        ),
    )
