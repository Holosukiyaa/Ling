"""MCP adapter for renewing the controller lease."""

from typing import Any

from ling.application.commands.renew_controller_lease import execute
from ling.application.dto import RenewControllerLeaseCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, require_text, validated
from ling.interfaces.mcp.schemas import RenewControllerLeaseInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Extend the lease held by the attached commander session."""

    parsed, error = validated(RenewControllerLeaseInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, RenewControllerLeaseInput)
    actor, missing = require_text(parsed.actor_slot_id, deps, "actor_slot_id")
    if missing is not None or actor is None:
        return missing or {}
    session_id, missing_session = require_text(deps.attachment.session_id, deps, "session_id")
    if missing_session is not None or session_id is None:
        return missing_session or {}
    return call_use_case(
        deps,
        execute,
        RenewControllerLeaseCommand(
            actor_slot_id=actor,
            session_id=session_id,
            ttl_seconds=deps.controller_lease_ttl_seconds,
            operation_id=parsed.operation_id,
        ),
    )
