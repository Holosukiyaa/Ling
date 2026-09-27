"""Generic MCP stdio server. It does not start an agent or choose a model."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel

from ling.application.ports.observability import RUNTIME_EVENT_SCHEMA, RuntimeEvent
from ling.interfaces.mcp.adapter import ToolDeps, gate_attachment, redact_arguments
from ling.interfaces.mcp.schemas import (
    TOOL_OUTPUT_SCHEMA,
    AbandonClaimInput,
    AcquireControllerLeaseInput,
    AcquireFileLockInput,
    AttachInput,
    ClaimInput,
    ConsumeInput,
    DashboardInput,
    DetachInput,
    DispatchInput,
    HeartbeatInput,
    ProvisionSlotInput,
    RegisterSlotInput,
    ReleaseControllerLeaseInput,
    RenewControllerLeaseInput,
    ReviewInput,
    SubmitInput,
)
from ling.interfaces.mcp.tools.abandon_claim import handle as abandon_claim
from ling.interfaces.mcp.tools.acquire_file_lock import handle as acquire_file_lock
from ling.interfaces.mcp.tools.attach import handle as attach
from ling.interfaces.mcp.tools.claim import handle as claim
from ling.interfaces.mcp.tools.consume import handle as consume
from ling.interfaces.mcp.tools.dashboard import handle as dashboard
from ling.interfaces.mcp.tools.detach import handle as detach
from ling.interfaces.mcp.tools.dispatch import handle as dispatch
from ling.interfaces.mcp.tools.heartbeat import handle as heartbeat
from ling.interfaces.mcp.tools.acquire_controller_lease import handle as acquire_controller_lease
from ling.interfaces.mcp.tools.provision_slot import handle as provision_slot
from ling.interfaces.mcp.tools.register_slot import handle as register_slot
from ling.interfaces.mcp.tools.release_controller_lease import handle as release_controller_lease
from ling.interfaces.mcp.tools.renew_controller_lease import handle as renew_controller_lease
from ling.interfaces.mcp.tools.review import handle as review
from ling.interfaces.mcp.tools.submit import handle as submit

logger = logging.getLogger(__name__)

_SILENT_TOOLS = frozenset({"ling_attach", "ling_detach", "ling_dashboard"})


@dataclass(frozen=True, slots=True)
class _Tool:
    name: str
    description: str
    model: type[BaseModel]
    handle: Callable[[dict[str, Any], ToolDeps], dict[str, Any]]


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "ling_register_slot",
        "Register a local slot from a template id and an attachment token.",
        RegisterSlotInput,
        register_slot,
    ),
    _Tool(
        "ling_attach",
        "Bind this MCP session to a slot by checking its attachment token.",
        AttachInput,
        attach,
    ),
    _Tool(
        "ling_detach",
        "Revoke this MCP session. A repeated call succeeds.",
        DetachInput,
        detach,
    ),
    _Tool(
        "ling_heartbeat",
        "Record that a local slot is online and store its heartbeat time.",
        HeartbeatInput,
        heartbeat,
    ),
    _Tool(
        "ling_dispatch",
        "Dispatch one local ticket when the issuer may manage the target template.",
        DispatchInput,
        dispatch,
    ),
    _Tool(
        "ling_claim",
        "Claim one queued local ticket for a worker slot.",
        ClaimInput,
        claim,
    ),
    _Tool(
        "ling_abandon_claim",
        "Abandon a claimed ticket, return it to queue 1, and release its file lock.",
        AbandonClaimInput,
        abandon_claim,
    ),
    _Tool(
        "ling_submit",
        "Submit one claimed local ticket for review.",
        SubmitInput,
        submit,
    ),
    _Tool(
        "ling_review",
        "Record a checker decision of accept or reject.",
        ReviewInput,
        review,
    ),
    _Tool(
        "ling_consume",
        "Consume one reviewed local ticket and release its locks.",
        ConsumeInput,
        consume,
    ),
    _Tool(
        "ling_acquire_file_lock",
        "Record a local file lock for paths held by a claimed worker.",
        AcquireFileLockInput,
        acquire_file_lock,
    ),
    _Tool(
        "ling_dashboard",
        "Return the local slots, tickets, queues, locks, and heartbeats.",
        DashboardInput,
        dashboard,
    ),
    _Tool(
        "ling_acquire_controller_lease",
        "Acquire the single controller lease for the attached commander session.",
        AcquireControllerLeaseInput,
        acquire_controller_lease,
    ),
    _Tool(
        "ling_renew_controller_lease",
        "Extend the controller lease held by the attached commander session.",
        RenewControllerLeaseInput,
        renew_controller_lease,
    ),
    _Tool(
        "ling_release_controller_lease",
        "Release the controller lease held by the attached commander session.",
        ReleaseControllerLeaseInput,
        release_controller_lease,
    ),
    _Tool(
        "ling_provision_slot",
        "Provision or rotate another slot credential while holding the controller lease.",
        ProvisionSlotInput,
        provision_slot,
    ),
)

_TOOLS_BY_NAME = {tool.name: tool for tool in _TOOLS}


def build_server(deps: ToolDeps) -> Server:
    """Register the local tools on an official MCP server."""

    server: Server = Server(
        "ling",
        version="0.0.0",
        instructions=(
            "Local task and permission kernel. The caller connects to this server. "
            "This server does not start an agent or choose a model. "
            "ling_register_slot, ling_attach, and ling_detach work before a session "
            "is attached. Other tools use the attached slot. "
            "A worker session may call only ling_heartbeat, ling_dashboard, "
            "ling_claim, ling_abandon_claim, ling_submit, ling_acquire_file_lock, "
            "ling_attach, and ling_detach. Its dashboard shows only its own slot "
            "and tickets targeted at that slot. "
            "Only the attached codex-commander session can hold the controller lease "
            "and provision slot credentials."
        ),
    )
    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        started = time.perf_counter()
        try:
            listed = [
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.model.model_json_schema(),
                    outputSchema=TOOL_OUTPUT_SCHEMA,
                )
                for tool in _TOOLS
            ]
        except Exception:
            _record(deps, "tools/list", _internal_payload(deps), False, started)
            raise
        _record(
            deps,
            "tools/list",
            {"ok": True, "operation_id": None, "error_code": None, "ticket_id": None, "state": None, "queue": None},
            False,
            started,
        )
        return listed

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        payload = invoke_tool(deps, name, arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload))],
            structuredContent=payload,
            isError=not bool(payload.get("ok")),
        )

    return server


def invoke_tool(deps: ToolDeps, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Run one tool call, including the attachment gate, event, and observer."""

    started = time.perf_counter()
    replay = False
    raw = arguments or {}
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        payload = {
            "ok": False,
            "operation_id": deps.ids.new_operation_id(),
            "ticket_id": None,
            "state": None,
            "queue": None,
            "error_code": "not_found",
            "message": "unknown tool",
        }
    else:
        try:
            refusal, bound = gate_attachment(deps, name, raw)
            if refusal is not None:
                payload = refusal
            else:
                payload = tool.handle(bound, deps)
        except Exception:
            logger.exception("tool %s failed", name)
            payload = _internal_payload(deps)
        else:
            replay = bool(payload.pop("replay", False))
    _record(deps, name, payload, replay, started)
    if not replay:
        _observe(deps, name, raw, payload)
    return payload


def _observe(deps: ToolDeps, name: str, arguments: dict[str, Any], payload: dict[str, Any]) -> None:
    """Hand a successful business result to the observer. Attach, detach, and dashboard stay local."""

    if name in _SILENT_TOOLS or not payload.get("ok"):
        return
    try:
        deps.observer.observe(name, redact_arguments(arguments), payload)
    except Exception:
        logger.exception("coordinator observation failed")


def _internal_payload(deps: ToolDeps) -> dict[str, Any]:
    return {
        "ok": False,
        "operation_id": deps.ids.new_operation_id(),
        "ticket_id": None,
        "state": None,
        "queue": None,
        "error_code": "internal",
        "message": "request failed",
    }


def _record(
    deps: ToolDeps,
    tool: str,
    payload: dict[str, Any],
    replay: bool,
    started: float,
) -> None:
    """Write one local runtime event. A sink failure leaves the MCP payload unchanged."""

    duration_ms = int((time.perf_counter() - started) * 1000)
    if duration_ms < 0:
        duration_ms = 0
    event = RuntimeEvent(
        schema=RUNTIME_EVENT_SCHEMA,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        tool=tool,
        operation_id=_text(payload.get("operation_id")),
        ok=bool(payload.get("ok")),
        error_code=_text(payload.get("error_code")),
        ticket_id=_text(payload.get("ticket_id")),
        state=_text(payload.get("state")),
        queue=_queue(payload.get("queue")),
        replay=replay,
        duration_ms=duration_ms,
    )
    try:
        deps.event_sink.record(event)
    except Exception:
        logger.exception("runtime event failed")


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value


def _queue(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


async def run_stdio(server: Server) -> None:
    """Serve MCP on stdio. Protocol bytes stay on stdout."""

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
