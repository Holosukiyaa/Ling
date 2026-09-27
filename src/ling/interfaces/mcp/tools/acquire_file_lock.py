"""MCP adapter for a local file lock."""

from typing import Any

from ling.application.commands.acquire_file_lock import execute
from ling.application.dto import AcquireFileLockCommand
from ling.interfaces.mcp.adapter import ToolDeps, call_use_case, validated
from ling.interfaces.mcp.schemas import AcquireFileLockInput


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Validate arguments and record one local file lock."""

    parsed, error = validated(AcquireFileLockInput, arguments, deps)
    if error is not None or parsed is None:
        return error or {}
    assert isinstance(parsed, AcquireFileLockInput)
    return call_use_case(
        deps,
        execute,
        AcquireFileLockCommand(
            actor_slot_id=parsed.actor_slot_id,
            ticket_id=parsed.ticket_id,
            paths=tuple(parsed.paths),
            operation_id=parsed.operation_id,
        ),
    )
