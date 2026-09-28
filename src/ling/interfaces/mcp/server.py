"""Generic MCP stdio server. It does not start an agent or choose a model."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import anyio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel

from ling.application.ports.observability import (
    RUNTIME_EVENT_SCHEMA,
    RUNTIME_LIFECYCLE_SCHEMA,
    LifecycleEvent,
    RuntimeEvent,
)
from ling.application.queries.failure import present
from ling.interfaces.mcp.adapter import ToolDeps, gate_attachment, redact_arguments
from ling.interfaces.mcp.request_context import bind_request_id, warn_failure
from ling.interfaces.mcp.resources import (
    _exclusive,
    clear_subscriptions,
    note_session,
    ping_connection,
    register_resources,
    watch_queue,
)
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
    DiagnosticsInput,
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
from ling.interfaces.mcp.tools.acquire_controller_lease import handle as acquire_controller_lease
from ling.interfaces.mcp.tools.acquire_file_lock import handle as acquire_file_lock
from ling.interfaces.mcp.tools.attach import handle as attach
from ling.interfaces.mcp.tools.claim import handle as claim
from ling.interfaces.mcp.tools.consume import handle as consume
from ling.interfaces.mcp.tools.dashboard import handle as dashboard
from ling.interfaces.mcp.tools.detach import handle as detach
from ling.interfaces.mcp.tools.diagnostics import handle as diagnostics
from ling.interfaces.mcp.tools.dispatch import handle as dispatch
from ling.interfaces.mcp.tools.heartbeat import handle as heartbeat
from ling.interfaces.mcp.tools.provision_slot import handle as provision_slot
from ling.interfaces.mcp.tools.register_slot import handle as register_slot
from ling.interfaces.mcp.tools.release_controller_lease import handle as release_controller_lease
from ling.interfaces.mcp.tools.renew_controller_lease import handle as renew_controller_lease
from ling.interfaces.mcp.tools.review import handle as review
from ling.interfaces.mcp.tools.submit import handle as submit

logger = logging.getLogger(__name__)

_SILENT_TOOLS = frozenset({"ling_attach", "ling_detach", "ling_dashboard", "ling_diagnostics"})
_BINDING_TOOLS = frozenset({"ling_attach", "ling_detach"})
_HIDDEN_SLOT_CODES = frozenset({"attachment_required", "attachment_rejected"})


@dataclass(frozen=True, slots=True)
class _Tool:
    name: str
    description: str
    model: type[BaseModel]
    handle: Callable[[dict[str, Any], ToolDeps], dict[str, Any]]


_TOOLS: tuple[_Tool, ...] = (
    _Tool("ling_register_slot", "Register a local slot from a template id and an attachment token.", RegisterSlotInput, register_slot),
    _Tool("ling_attach", "Bind this MCP session to a slot by checking its attachment token.", AttachInput, attach),
    _Tool("ling_detach", "Revoke this MCP session. A repeated call succeeds.", DetachInput, detach),
    _Tool("ling_heartbeat", "Record a slot heartbeat. Dashboard presence is calculated from that time.", HeartbeatInput, heartbeat),
    _Tool("ling_dispatch", "Dispatch one local ticket when the issuer may manage the target template.", DispatchInput, dispatch),
    _Tool("ling_claim", "Claim one queued local ticket for a worker slot.", ClaimInput, claim),
    _Tool("ling_abandon_claim", "Abandon a claimed ticket, return it to queue 1, and release its file lock.", AbandonClaimInput, abandon_claim),
    _Tool("ling_submit", "Submit one claimed local ticket for review.", SubmitInput, submit),
    _Tool("ling_review", "Record a checker decision of accept or reject.", ReviewInput, review),
    _Tool("ling_consume", "Consume one reviewed local ticket and release its locks.", ConsumeInput, consume),
    _Tool("ling_acquire_file_lock", "Record a local file lock for paths held by a claimed worker.", AcquireFileLockInput, acquire_file_lock),
    _Tool("ling_dashboard", "Return the local slots, tickets, queues, locks, and heartbeats.", DashboardInput, dashboard),
    _Tool("ling_acquire_controller_lease", "Acquire the single controller lease for the attached commander session.", AcquireControllerLeaseInput, acquire_controller_lease),
    _Tool("ling_renew_controller_lease", "Extend the controller lease held by the attached commander session.", RenewControllerLeaseInput, renew_controller_lease),
    _Tool("ling_release_controller_lease", "Release the controller lease held by the attached commander session.", ReleaseControllerLeaseInput, release_controller_lease),
    _Tool("ling_provision_slot", "Provision or rotate another slot credential while holding the controller lease.", ProvisionSlotInput, provision_slot),
    _Tool("ling_diagnostics", "Read a redacted diagnostic report for this session. It does not change tickets, locks, or leases.", DiagnosticsInput, diagnostics),
)

_TOOLS_BY_NAME = {tool.name: tool for tool in _TOOLS}


def catalog_identifier() -> str:
    """Stable id of the public tool names. It is not a credential."""

    names = "\n".join(tool.name for tool in _TOOLS)
    return hashlib.sha256(names.encode("utf-8")).hexdigest()


def build_server(deps: ToolDeps) -> Server:
    """Register the local tools on an official MCP server."""

    deps.runtime_state["catalog_id"] = catalog_identifier()
    deps.runtime_state["record_lifecycle"] = lambda **kwargs: _lifecycle(deps, **kwargs)
    deps.runtime_state["bind_request"] = lambda bound, tool: _request_binding(deps, bound, tool)
    deps.runtime_state["close_request"] = lambda jsonrpc_id: _finish_tracked(deps, jsonrpc_id)
    server: Server = Server(
        "ling",
        version="0.0.0",
        instructions=(
            "Local task and permission kernel. The caller connects to this server. "
            "This server does not start an agent or choose a model. "
            "tools/list is the full stable catalog from the first response. "
            "Attaching changes authorization and visibility, not the catalog. "
            "A worker session may call only ling_heartbeat, ling_dashboard, "
            "ling_diagnostics, ling_claim, ling_abandon_claim, ling_submit, "
            "ling_acquire_file_lock, ling_attach, and ling_detach. "
            "Its dashboard and queue resource show only its own slot and tickets "
            "targeted at that slot. "
            "Only the attached codex-commander session can hold the controller lease "
            "and provision slot credentials. "
            "ling_diagnostics is read-only. Queue notifications are not claims."
        ),
    )
    register_resources(server, deps)

    @server.list_tools()
    async def list_tools() -> types.ListToolsResult:
        note_session(server, deps)
        request_id, started, jsonrpc_id = _request_binding(deps, server, "tools/list")
        recorded = False

        def run() -> list[types.Tool]:
            nonlocal recorded
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
            except Exception as exc:
                logger.warning("tools/list failed request_id=%s type=%s", request_id, type(exc).__name__)
                payload = _internal_payload(deps)
                _record(deps, "tools/list", payload, False, started)
                _lifecycle(
                    deps,
                    kind="finished",
                    request_id=request_id,
                    tool="tools/list",
                    payload=payload,
                    started=started,
                    stage="execution",
                    commit_state="unknown",
                )
                recorded = True
                raise
            payload = {"ok": True, "operation_id": None, "error_code": None, "ticket_id": None, "state": None, "queue": None, "_replay": False}
            _record(deps, "tools/list", payload, False, started)
            _lifecycle(
                deps,
                kind="finished",
                request_id=request_id,
                tool="tools/list",
                payload=payload,
                raw=payload,
                started=started,
                stage="protocol",
                commit_state="not_started",
            )
            recorded = True
            return listed

        try:
            with bind_request_id(request_id):
                listed = await _exclusive(deps, run)
        finally:
            if recorded:
                _finish_tracked(deps, jsonrpc_id)
        return types.ListToolsResult.model_validate(
            {"tools": [tool.model_dump(by_alias=True, exclude_none=True) for tool in listed], "_meta": {"request_id": request_id}}
        )

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        note_session(server, deps)
        request_id, started, jsonrpc_id = _request_binding(deps, server, name)
        recorded = False

        def run() -> tuple[dict[str, Any], dict[str, Any]]:
            nonlocal recorded
            payload = invoke_tool(deps, name, arguments, request_id=request_id)
            if name in _BINDING_TOOLS and payload.get("ok"):
                clear_subscriptions(deps)
            body, meta = present(payload, request_id=request_id)
            _remember(deps, request_id, name, body, meta, started)
            _lifecycle(
                deps,
                kind="finished",
                request_id=request_id,
                tool=name,
                payload=body,
                raw=payload,
                started=started,
                stage=str(meta.get("stage") or ""),
                commit_state=str(meta.get("commit_state") or ""),
            )
            recorded = True
            return body, meta

        try:
            with bind_request_id(request_id):
                body, meta = await _exclusive(deps, run)
        finally:
            if recorded:
                _finish_tracked(deps, jsonrpc_id)
        return types.CallToolResult.model_validate(
            {
                "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
                "structuredContent": body,
                "isError": not bool(body.get("ok")),
                "_meta": meta,
            }
        )

    return server


def invoke_tool(
    deps: ToolDeps,
    name: str,
    arguments: dict[str, Any] | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run one tool call, including the attachment gate, event, and observer."""

    with bind_request_id(request_id):
        return _invoke_tool(deps, name, arguments, request_id)


def _invoke_tool(
    deps: ToolDeps,
    name: str,
    arguments: dict[str, Any] | None,
    request_id: str | None,
) -> dict[str, Any]:
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
            "_commit_state": "not_started",
            "_reason_code": "unknown_tool",
        }
    else:
        try:
            refusal, bound = gate_attachment(deps, name, raw)
            if refusal is not None:
                payload = refusal
            else:
                payload = tool.handle(bound, deps)
        except Exception as exc:
            logger.warning("tool failed request_id=%s type=%s", request_id or "unknown", type(exc).__name__)
            payload = _internal_payload(deps)
        else:
            replay = bool(payload.pop("replay", False))
    payload["_replay"] = replay
    _record(deps, name, payload, replay, started)
    if not replay:
        _observe(deps, name, raw, payload)
    return payload


def _observe(deps: ToolDeps, name: str, arguments: dict[str, Any], payload: dict[str, Any]) -> None:
    """Hand a successful business result to the observer. Failures stay local."""

    if name in _SILENT_TOOLS or not payload.get("ok"):
        return
    try:
        deps.observer.observe(name, redact_arguments(arguments), _public_body(payload))
    except Exception as exc:
        warn_failure(logger, "coordinator observation failed", exc)


def _public_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


def _internal_payload(deps: ToolDeps) -> dict[str, Any]:
    return {
        "ok": False,
        "operation_id": deps.ids.new_operation_id(),
        "ticket_id": None,
        "state": None,
        "queue": None,
        "error_code": "internal",
        "message": "request failed",
        "_commit_state": "unknown",
        "_reason_code": "unexpected",
    }


def _record(
    deps: ToolDeps,
    tool: str,
    payload: dict[str, Any],
    replay: bool,
    started: float,
) -> None:
    """Write one v1 summary. A sink failure leaves the MCP payload unchanged."""

    event = RuntimeEvent(
        schema=RUNTIME_EVENT_SCHEMA,
        recorded_at=deps.clock.now().isoformat(),
        tool=tool,
        operation_id=_text(payload.get("operation_id")),
        ok=bool(payload.get("ok")),
        error_code=_text(payload.get("error_code")),
        ticket_id=_text(payload.get("ticket_id")),
        state=_text(payload.get("state")),
        queue=_queue(payload.get("queue")),
        replay=replay,
        duration_ms=_duration(started),
    )
    try:
        deps.event_sink.record(event)
    except Exception as exc:
        warn_failure(logger, "runtime event failed", exc)


def _lifecycle(
    deps: ToolDeps,
    *,
    kind: str,
    request_id: str | None = None,
    tool: str | None = None,
    payload: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    started: float | None = None,
    ok: bool | None = None,
    error_code: str | None = None,
    stage: str | None = None,
    commit_state: str | None = None,
) -> None:
    """Write one v2 fact. This never changes the business result."""

    source = raw if raw is not None else payload
    public = payload or {}
    if kind == "received":
        deps.runtime_state["inbound_observed"] = True
    if ok is None and source is not None:
        ok = bool(source.get("ok"))
    if error_code is None and source is not None:
        error_code = _text(source.get("error_code"))
    if not stage:
        stage = _text(public.get("stage"))
    if commit_state not in {"not_started", "not_committed", "committed", "unknown"}:
        marked = None if source is None else source.get("_commit_state")
        if marked in {"not_started", "not_committed", "committed", "unknown"}:
            commit_state = str(marked)
        elif public.get("commit_state") in {"not_started", "not_committed", "committed", "unknown"}:
            commit_state = str(public.get("commit_state"))
        else:
            commit_state = None
    reason_code = _text(public.get("reason_code"))
    if reason_code is None and source is not None:
        reason_code = _text(source.get("_reason_code"))
    retryable = public.get("retryable") if isinstance(public.get("retryable"), bool) else None
    next_action = _text(public.get("next_action"))
    replay_flag = None
    if source is not None and isinstance(source.get("_replay"), bool):
        replay_flag = source.get("_replay")
    elif source is not None and isinstance(source.get("replay"), bool):
        replay_flag = source.get("replay")
    event = LifecycleEvent(
        schema=RUNTIME_LIFECYCLE_SCHEMA,
        kind=kind,
        recorded_at=deps.clock.now().isoformat(),
        instance_id=deps.runtime_instance_id or "runtime",
        request_id=request_id,
        tool=tool,
        slot_id=_slot_for_log(deps, source),
        operation_id=None if source is None else _text(source.get("operation_id")),
        ok=ok,
        error_code=error_code,
        ticket_id=None if source is None else _text(source.get("ticket_id")),
        state=None if source is None else _text(source.get("state")),
        queue=None if source is None else _queue(source.get("queue")),
        stage=stage or None,
        commit_state=commit_state or None,
        duration_ms=None if started is None else _duration(started),
        replay=replay_flag if isinstance(replay_flag, bool) else None,
        reason_code=reason_code,
        retryable=retryable,
        next_action=next_action,
    )
    try:
        deps.event_sink.record(event)
    except Exception as exc:
        warn_failure(logger, "runtime lifecycle event failed", exc)


def _remember(
    deps: ToolDeps,
    request_id: str,
    tool: str,
    body: dict[str, Any],
    meta: dict[str, Any],
    started: float,
) -> None:
    code = body.get("error_code")
    slot_id = None
    if code not in _HIDDEN_SLOT_CODES and isinstance(deps.attachment.slot_id, str):
        slot_id = deps.attachment.slot_id
    deps.recent_requests.append(
        {
            "request_id": request_id,
            "tool": tool,
            "slot_id": slot_id,
            "operation_id": body.get("operation_id") if isinstance(body.get("operation_id"), str) else None,
            "ok": bool(body.get("ok")),
            "error_code": code if isinstance(code, str) else None,
            "reason_code": body.get("reason_code") if isinstance(body.get("reason_code"), str) else meta.get("reason_code"),
            "stage": body.get("stage") if isinstance(body.get("stage"), str) else meta.get("stage"),
            "retryable": body.get("retryable") if isinstance(body.get("retryable"), bool) else meta.get("retryable"),
            "next_action": body.get("next_action") if isinstance(body.get("next_action"), str) else meta.get("next_action"),
            "commit_state": meta.get("commit_state") if isinstance(meta.get("commit_state"), str) else body.get("commit_state"),
            "duration_ms": _duration(started),
            "recorded_at": deps.clock.now().isoformat(),
        }
    )
    if len(deps.recent_requests) > 200:
        del deps.recent_requests[:-200]


def _slot_for_log(deps: ToolDeps, payload: dict[str, Any] | None) -> str | None:
    if payload is not None and payload.get("error_code") in _HIDDEN_SLOT_CODES:
        return None
    slot_id = deps.attachment.slot_id
    if isinstance(slot_id, str) and slot_id.strip():
        return slot_id
    return None


def _new_request_id() -> str:
    return uuid4().hex


def _duration(started: float) -> int:
    duration_ms = int((time.perf_counter() - started) * 1000)
    if duration_ms < 0:
        return 0
    return duration_ms


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value


def _queue(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def record_process_event(deps: ToolDeps, kind: str) -> None:
    """Record a process or connection boundary. This does not touch business rows."""

    _lifecycle(deps, kind=kind, tool="ling", commit_state="not_started")


async def run_stdio(server: Server, deps: ToolDeps) -> None:
    """Serve MCP on stdio. Protocol bytes stay on stdout."""

    deps.runtime_state["call_lock"] = anyio.Lock()
    record_process_event(deps, "connection_started")
    try:
        async with stdio_server() as (read_stream, write_stream):
            forwarded_send, forwarded_read = anyio.create_memory_object_stream(32)
            observed_write = _OutboundTap(write_stream, deps)
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(_forward_client_messages, read_stream, forwarded_send, deps)
                tasks.start_soon(ping_connection, server, deps)
                tasks.start_soon(watch_queue, server, deps)
                options = server.create_initialization_options(
                    notification_options=NotificationOptions(
                        tools_changed=False,
                        resources_changed=True,
                    ),
                )
                resources = options.capabilities.resources
                if resources is not None:
                    resources.subscribe = True
                try:
                    await server.run(forwarded_read, observed_write, options)
                finally:
                    tasks.cancel_scope.cancel()
    finally:
        record_process_event(deps, "connection_stopped")


async def _forward_client_messages(read_stream: Any, send_stream: Any, deps: ToolDeps) -> None:
    """Copy client frames and note initialize without blocking later protocol pings."""

    try:
        async for item in read_stream:
            if not isinstance(item, Exception):
                _observe_inbound(deps, item)
            await send_stream.send(item)
    except Exception:
        logger.warning("client message forward failed")
    finally:
        try:
            await send_stream.aclose()
        except Exception:
            logger.warning("client message forward close failed")


def _observe_inbound(deps: ToolDeps, item: object) -> None:
    """Record that a request arrived. Completion waits for the real response."""

    try:
        method, params, jsonrpc_id = _message_parts(item)
    except Exception:
        logger.warning("inbound frame was not readable")
        return
    if not isinstance(method, str) or not method:
        return
    deps.runtime_state["inbound_observed"] = True
    if jsonrpc_id is None:
        return
    request_id = _new_request_id()
    started = time.perf_counter()
    tool = _inbound_tool(method, params)
    pending = _pending_requests(deps)
    pending[_id_key(jsonrpc_id)] = {
        "request_id": request_id,
        "tool": tool,
        "method": method,
        "started": started,
    }
    if len(pending) > 200:
        for key in list(pending)[:50]:
            pending.pop(key, None)
    _lifecycle(
        deps,
        kind="received",
        request_id=request_id,
        tool=tool,
        stage="protocol",
        commit_state="not_started",
    )


def _observe_outbound(deps: ToolDeps, item: object) -> None:
    """Finish a request from the response that was actually sent."""

    try:
        root = _message_root(item)
    except Exception:
        logger.warning("outbound frame was not readable")
        return
    if root is None or not isinstance(root, types.JSONRPCResponse | types.JSONRPCError):
        return
    jsonrpc_id = getattr(root, "id", None)
    tracked = _tracked(deps, jsonrpc_id)
    if not isinstance(tracked, dict):
        return
    request_id = tracked.get("request_id") if isinstance(tracked.get("request_id"), str) else None
    tool = tracked.get("tool") if isinstance(tracked.get("tool"), str) else "unknown"
    method = tracked.get("method") if isinstance(tracked.get("method"), str) else ""
    started = tracked.get("started") if isinstance(tracked.get("started"), float) else None
    if isinstance(root, types.JSONRPCError):
        code = getattr(root.error, "code", None)
        logger.warning("request failed request_id=%s type=jsonrpc_error code=%s", request_id, code)
        _lifecycle(
            deps,
            kind="finished",
            request_id=request_id,
            tool=tool,
            ok=False,
            error_code="invalid_params" if code == -32602 else "protocol_failed",
            started=started,
            stage="protocol",
            commit_state="not_started",
        )
        _finish_tracked(deps, jsonrpc_id)
        return
    if method == "initialize":
        result = root.result if isinstance(root.result, dict) else {}
        version = result.get("protocolVersion")
        if isinstance(version, str) and version.strip():
            deps.runtime_state["protocol"] = version.strip()
        _lifecycle(
            deps,
            kind="finished",
            request_id=request_id,
            tool="initialize",
            ok=True,
            started=started,
            stage="protocol",
            commit_state="not_started",
        )
        _finish_tracked(deps, jsonrpc_id)
        return
    _lifecycle(
        deps,
        kind="finished",
        request_id=request_id,
        tool=tool,
        ok=True,
        started=started,
        stage="protocol",
        commit_state="not_started",
    )
    _finish_tracked(deps, jsonrpc_id)


class _OutboundTap:
    """Forward stdout frames and observe them without taking the call lock."""

    def __init__(self, inner: Any, deps: ToolDeps) -> None:
        self._inner = inner
        self._deps = deps

    async def send(self, item: object) -> None:
        try:
            _observe_outbound(self._deps, item)
        except Exception:
            logger.warning("outbound frame was not readable")
        await self._inner.send(item)

    async def __aenter__(self) -> "_OutboundTap":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> object:
        return await self._inner.__aexit__(exc_type, exc, traceback)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _message_root(item: object) -> object:
    message = getattr(item, "message", None)
    return getattr(message, "root", None)


def _message_parts(item: object) -> tuple[object, object, object]:
    root = _message_root(item)
    method = getattr(root, "method", None)
    if not isinstance(method, str) or not method:
        return None, None, None
    return method, getattr(root, "params", None), getattr(root, "id", None)


def _message_method(item: object) -> tuple[object, object]:
    method, params, _jsonrpc_id = _message_parts(item)
    return method, params


def _inbound_tool(method: str, params: object) -> str:
    if method == "tools/call" and isinstance(params, dict):
        name = params.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return method


def _pending_requests(deps: ToolDeps) -> dict[str, dict[str, object]]:
    found = deps.runtime_state.get("requests_by_jsonrpc_id")
    if not isinstance(found, dict):
        found = {}
        deps.runtime_state["requests_by_jsonrpc_id"] = found
    return found


def _id_key(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "none"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, str):
        try:
            return f"i:{int(value)}"
        except ValueError:
            return f"s:{value}"
    return f"o:{type(value).__name__}"


def _tracked(deps: ToolDeps, jsonrpc_id: object) -> dict[str, object] | None:
    if jsonrpc_id is None:
        return None
    found = _pending_requests(deps).get(_id_key(jsonrpc_id))
    if isinstance(found, dict):
        return found
    return None


def _finish_tracked(deps: ToolDeps, jsonrpc_id: object) -> None:
    if jsonrpc_id is None:
        return
    _pending_requests(deps).pop(_id_key(jsonrpc_id), None)


def _request_binding(deps: ToolDeps, server: Server, tool: str) -> tuple[str, float, object]:
    """Reuse the entry record when present. Otherwise record before the call lock."""

    jsonrpc_id = None
    try:
        jsonrpc_id = server.request_context.request_id
    except Exception:
        jsonrpc_id = None
    tracked = _tracked(deps, jsonrpc_id)
    if isinstance(tracked, dict) and isinstance(tracked.get("request_id"), str):
        started = tracked.get("started")
        if not isinstance(started, float):
            started = time.perf_counter()
        return tracked["request_id"], started, jsonrpc_id
    request_id = _new_request_id()
    started = time.perf_counter()
    _lifecycle(
        deps,
        kind="received",
        request_id=request_id,
        tool=tool,
        stage="protocol",
        commit_state="not_started",
    )
    return request_id, started, jsonrpc_id
