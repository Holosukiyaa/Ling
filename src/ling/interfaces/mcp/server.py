"""Generic MCP stdio server. It does not start an agent or choose a model."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel

from ling.interfaces.mcp.adapter import ToolDeps
from ling.interfaces.mcp.schemas import (
    TOOL_OUTPUT_SCHEMA,
    AbandonClaimInput,
    AcquireFileLockInput,
    ClaimInput,
    ConsumeInput,
    DashboardInput,
    DispatchInput,
    HeartbeatInput,
    RegisterSlotInput,
    ReviewInput,
    SubmitInput,
)
from ling.interfaces.mcp.tools.abandon_claim import handle as abandon_claim
from ling.interfaces.mcp.tools.acquire_file_lock import handle as acquire_file_lock
from ling.interfaces.mcp.tools.claim import handle as claim
from ling.interfaces.mcp.tools.consume import handle as consume
from ling.interfaces.mcp.tools.dashboard import handle as dashboard
from ling.interfaces.mcp.tools.dispatch import handle as dispatch
from ling.interfaces.mcp.tools.heartbeat import handle as heartbeat
from ling.interfaces.mcp.tools.register_slot import handle as register_slot
from ling.interfaces.mcp.tools.review import handle as review
from ling.interfaces.mcp.tools.submit import handle as submit

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Tool:
    name: str
    description: str
    model: type[BaseModel]
    handle: Callable[[dict[str, Any], ToolDeps], dict[str, Any]]


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "ling_register_slot",
        "Register a local slot from a template id.",
        RegisterSlotInput,
        register_slot,
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
)


def build_server(deps: ToolDeps) -> Server:
    """Register the ten local tools on an official MCP server."""

    server: Server = Server(
        "ling",
        version="0.0.0",
        instructions=(
            "Local task and permission kernel. The caller connects to this server. "
            "This server does not start an agent or choose a model."
        ),
    )
    by_name = {tool.name: tool for tool in _TOOLS}

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.model.model_json_schema(),
                outputSchema=TOOL_OUTPUT_SCHEMA,
            )
            for tool in _TOOLS
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        tool = by_name.get(name)
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
                payload = tool.handle(arguments or {}, deps)
            except Exception:
                logger.exception("tool %s failed", name)
                payload = {
                    "ok": False,
                    "operation_id": deps.ids.new_operation_id(),
                    "ticket_id": None,
                    "state": None,
                    "queue": None,
                    "error_code": "internal",
                    "message": "request failed",
                }
            else:
                _observe(deps, name, arguments or {}, payload)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload))],
            structuredContent=payload,
            isError=not bool(payload.get("ok")),
        )

    return server


def _observe(deps: ToolDeps, name: str, arguments: dict[str, Any], payload: dict[str, Any]) -> None:
    """Hand a successful local result to the observer. Observation cannot change it."""

    if not payload.get("ok"):
        return
    try:
        deps.observer.observe(name, arguments, payload)
    except Exception:
        logger.exception("coordinator observation failed")


async def run_stdio(server: Server) -> None:
    """Serve MCP on stdio. Protocol bytes stay on stdout."""

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
