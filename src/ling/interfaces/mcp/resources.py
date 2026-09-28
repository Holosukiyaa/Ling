"""Queue resource, subscription checks, and protocol ping.

Notifications mean the caller's visible snapshot changed. They do not claim
or finish a ticket, and they do not refresh the tool catalog.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import AnyUrl

from ling.application.ports.storage import StorageFailure
from ling.application.queries.dashboard import execute as load_dashboard
from ling.application.queries.diagnostics import limited_report, queue_token
from ling.application.queries.resolve_attachment import execute as resolve_attachment
from ling.interfaces.mcp.adapter import ToolDeps, _safe_close, open_unit
from ling.interfaces.mcp.request_context import bind_request_id, warn_failure

_TRANSIENT = object()

logger = logging.getLogger(__name__)

QUEUE_URI = "ling://queue"
_QUEUE_URL = AnyUrl(QUEUE_URI)


def register_resources(server: Server, deps: ToolDeps) -> None:
    """Publish one fixed queue URI. The URI does not contain a session or token."""

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        _note_session(server, deps)
        request_id, started, jsonrpc_id = _bind(server, deps, "resources/list")
        with bind_request_id(request_id):
            try:
                listed = [
                    types.Resource(
                        uri=_QUEUE_URL,
                        name="queue",
                        description=(
                            "Visible Ling queue snapshot. Read and subscribe check the "
                            "current session. A change notification is not a claim or a completion."
                        ),
                        mimeType="application/json",
                    )
                ]
            except Exception as exc:
                logger.warning("resources/list failed request_id=%s type=%s", request_id, type(exc).__name__)
                _finish_resource(deps, request_id, started, "resources/list", ok=False, error_code="internal", stage="execution", commit_state="unknown")
                _close_request(deps, jsonrpc_id)
                raise
            _finish_resource(deps, request_id, started, "resources/list", ok=True, error_code=None, stage="protocol", commit_state="not_started")
            _close_request(deps, jsonrpc_id)
            return listed

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        _note_session(server, deps)
        request_id, started, jsonrpc_id = _bind(server, deps, "resources/read")
        with bind_request_id(request_id):
            try:
                body = await _exclusive(deps, lambda: _read_body(deps, str(uri), request_id))
            except Exception as exc:
                logger.warning("resources/read failed request_id=%s type=%s", request_id, type(exc).__name__)
                _finish_resource(deps, request_id, started, "resources/read", ok=False, error_code="internal", stage="execution", commit_state="unknown")
                _close_request(deps, jsonrpc_id)
                raise
            _finish_resource(
                deps,
                request_id,
                started,
                "resources/read",
                ok=body.get("ok") is True,
                error_code=body.get("error_code") if isinstance(body.get("error_code"), str) else None,
                stage=body.get("stage") if isinstance(body.get("stage"), str) else "execution",
                commit_state=body.get("commit_state") if isinstance(body.get("commit_state"), str) else "not_started",
            )
            _close_request(deps, jsonrpc_id)
            return [_contents(body)]

    @server.subscribe_resource()
    async def subscribe_resource(uri: AnyUrl) -> None:
        _note_session(server, deps)
        request_id, started, jsonrpc_id = _bind(server, deps, "resources/subscribe")
        with bind_request_id(request_id):
            try:
                if str(uri) != QUEUE_URI:
                    raise ValueError("unknown resource")
                session = _session(server)
                if session is None:
                    raise ValueError("attachment required")

                def store() -> str | None:
                    baseline = _baseline(deps)
                    if baseline is None:
                        return "attachment required"
                    identity, token = baseline
                    epoch = deps.runtime_state.get("subscription_epoch")
                    generation = epoch if isinstance(epoch, int) else 0
                    deps.subscriptions[str(id(session))] = {
                        "session": session,
                        "slot_id": identity[0],
                        "template_id": identity[1],
                        "fingerprint": token,
                        "generation": generation,
                    }
                    return None

                failure = await _exclusive(deps, store)
                if failure is not None:
                    raise ValueError(failure)
            except Exception as exc:
                logger.warning("resources/subscribe failed request_id=%s type=%s", request_id, type(exc).__name__)
                _finish_resource(deps, request_id, started, "resources/subscribe", ok=False, error_code="invalid_input", stage="validation", commit_state="not_started")
                _close_request(deps, jsonrpc_id)
                raise
            _finish_resource(deps, request_id, started, "resources/subscribe", ok=True, error_code=None, stage="protocol", commit_state="not_started")
            _close_request(deps, jsonrpc_id)

    @server.unsubscribe_resource()
    async def unsubscribe_resource(uri: AnyUrl) -> None:
        _note_session(server, deps)
        request_id, started, jsonrpc_id = _bind(server, deps, "resources/unsubscribe")
        with bind_request_id(request_id):
            try:
                if str(uri) == QUEUE_URI:
                    session = _session(server)
                    if session is not None:
                        await _exclusive(deps, lambda: deps.subscriptions.pop(str(id(session)), None))
            except Exception as exc:
                logger.warning("resources/unsubscribe failed request_id=%s type=%s", request_id, type(exc).__name__)
                _finish_resource(deps, request_id, started, "resources/unsubscribe", ok=False, error_code="internal", stage="execution", commit_state="unknown")
                _close_request(deps, jsonrpc_id)
                raise
            _finish_resource(deps, request_id, started, "resources/unsubscribe", ok=True, error_code=None, stage="protocol", commit_state="not_started")
            _close_request(deps, jsonrpc_id)


def clear_subscriptions(deps: ToolDeps) -> None:
    """Drop every subscriber and invalidate notices prepared under the old generation."""

    deps.subscriptions.clear()
    epoch = deps.runtime_state.get("subscription_epoch")
    deps.runtime_state["subscription_epoch"] = (epoch if isinstance(epoch, int) else 0) + 1


async def watch_queue(server: Server, deps: ToolDeps) -> None:
    """Poll committed rows for subscribers. A busy tool call skips this round."""

    del server
    while True:
        try:
            await anyio.sleep(deps.queue_poll_seconds)
            pending = await _exclusive(deps, lambda: _pending_notices(deps))
            for item in pending:
                await _deliver(deps, item)
        except Exception:
            logger.warning("queue watch failed")


async def ping_connection(server: Server, deps: ToolDeps) -> None:
    """Ping the attached client. A skipped or failed ping does not change business rows."""

    while True:
        try:
            await anyio.sleep(deps.ping_interval_seconds)
            session = deps.runtime_state.get("session")
            if session is None:
                continue
            send = getattr(session, "send_ping", None)
            if not callable(send):
                continue
            ok = False
            error_code = "protocol_failed"
            try:
                with anyio.fail_after(deps.ping_timeout_seconds):
                    await send()
                ok = True
                error_code = None
            except TimeoutError:
                error_code = "timeout"
            except Exception:
                error_code = "protocol_failed"
            await _exclusive(deps, lambda: _ping(deps, server, ok, error_code))
        except Exception:
            logger.warning("protocol ping failed")


def note_session(server: Server, deps: ToolDeps) -> None:
    """Remember the current MCP session object. The id is not stored."""

    _note_session(server, deps)


def _note_session(server: Server, deps: ToolDeps) -> None:
    try:
        deps.runtime_state["session"] = server.request_context.session
    except Exception:
        return


def _session(server: Server) -> object | None:
    try:
        return server.request_context.session
    except Exception:
        return None


async def _exclusive(deps: ToolDeps, function: Any) -> Any:
    """Run one database or log call off the protocol loop, serialized per connection."""

    lock = deps.runtime_state.get("call_lock")
    if lock is None:
        lock = anyio.Lock()
        deps.runtime_state["call_lock"] = lock
    async with lock:
        return await anyio.to_thread.run_sync(function)


def _request_id() -> str:
    from uuid import uuid4

    return uuid4().hex


def _protocol(deps: ToolDeps) -> str:
    value = deps.runtime_state.get("protocol")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _record(deps: ToolDeps, **kwargs: Any) -> None:
    record = deps.runtime_state.get("record_lifecycle")
    if not callable(record):
        return
    try:
        record(**kwargs)
    except Exception:
        logger.warning("resource lifecycle failed")


def _bind(server: Server, deps: ToolDeps, tool: str) -> tuple[str, float, object]:
    bind = deps.runtime_state.get("bind_request")
    if callable(bind):
        bound = bind(server, tool)
        if isinstance(bound, tuple) and len(bound) == 3:
            return bound
    request_id = _request_id()
    _record(deps, kind="received", request_id=request_id, tool=tool, stage="protocol", commit_state="not_started")
    return request_id, time.perf_counter(), None


def _close_request(deps: ToolDeps, jsonrpc_id: object) -> None:
    close = deps.runtime_state.get("close_request")
    if callable(close):
        close(jsonrpc_id)


def _finish_resource(
    deps: ToolDeps,
    request_id: str,
    started: float,
    tool: str,
    *,
    ok: bool,
    error_code: str | None,
    stage: str,
    commit_state: str,
) -> None:
    _record(
        deps,
        kind="finished",
        request_id=request_id,
        tool=tool,
        ok=ok,
        error_code=error_code,
        started=started,
        stage=stage,
        commit_state=commit_state,
    )


def _read_body(deps: ToolDeps, uri: str, request_id: str) -> dict[str, Any]:
    try:
        if uri != QUEUE_URI:
            return {"ok": False, "error_code": "not_found", "message": "unknown resource", "stage": "validation"}
        return _snapshot(deps)
    except Exception as exc:
        logger.warning("resource read failed request_id=%s type=%s", request_id, type(exc).__name__)
        return {"ok": False, "error_code": "internal", "message": "request failed", "stage": "execution", "commit_state": "unknown"}


def _baseline(deps: ToolDeps) -> tuple[tuple[str, str], str] | None:
    """Read the committed visible queue before subscribe succeeds."""

    identity = _identity(deps)
    if not isinstance(identity, tuple):
        return None
    body = _snapshot(deps)
    if body.get("ok") is not True:
        return None
    return identity, queue_token(list(body.get("tickets") or []))


def _pending_notices(deps: ToolDeps) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for key, sub in list(deps.subscriptions.items()):
        try:
            identity = _identity(deps)
            if identity is _TRANSIENT:
                continue
            if not isinstance(identity, tuple) or identity[0] != sub.get("slot_id"):
                deps.subscriptions.pop(key, None)
                continue
            body = _snapshot(deps)
            if body.get("ok") is not True:
                if body.get("error_code") in {"storage_busy", "storage_unavailable"}:
                    continue
                deps.subscriptions.pop(key, None)
                continue
            token = queue_token(list(body.get("tickets") or []))
            previous = sub.get("fingerprint")
            if previous == token:
                continue
            pending.append(
                {
                    "key": key,
                    "token": token,
                    "previous": previous,
                    "session": sub.get("session"),
                    "generation": sub.get("generation"),
                    "slot_id": sub.get("slot_id"),
                    "template_id": sub.get("template_id"),
                }
            )
        except Exception:
            logger.warning("queue subscriber failed")
    return pending


async def _deliver(deps: ToolDeps, item: dict[str, Any]) -> None:
    session = item.get("session")
    send = getattr(session, "send_resource_updated", None)
    if not callable(send):
        return
    if not await _exclusive(deps, lambda: _delivery_allowed(deps, item)):
        return
    try:
        await send(_QUEUE_URL)
    except Exception:
        logger.warning("queue notification failed")
        return
    await _exclusive(deps, lambda: _mark_delivered(deps, item))


def _delivery_allowed(deps: ToolDeps, item: dict[str, Any]) -> bool:
    """Recheck the subscription generation and the live session before sending."""

    current = deps.subscriptions.get(item.get("key"))
    if not _same_subscription(current, item):
        return False
    identity = _identity(deps)
    if not isinstance(identity, tuple):
        return False
    return identity[0] == current.get("slot_id") and identity[1] == current.get("template_id")


def _mark_delivered(deps: ToolDeps, item: dict[str, Any]) -> None:
    current = deps.subscriptions.get(item.get("key"))
    if not _same_subscription(current, item):
        return
    if current.get("fingerprint") == item.get("previous"):
        current["fingerprint"] = item.get("token")


def _same_subscription(current: object, item: dict[str, Any]) -> bool:
    if not isinstance(current, dict):
        return False
    if current.get("generation") != item.get("generation"):
        return False
    if current.get("session") is not item.get("session"):
        return False
    if current.get("slot_id") != item.get("slot_id"):
        return False
    if current.get("fingerprint") != item.get("previous"):
        return False
    return True


def _identity(deps: ToolDeps) -> tuple[str, str] | object | None:
    session_id = deps.attachment.session_id
    if not session_id:
        return None
    unit, failure = open_unit(deps, read_only=True)
    if failure is not None:
        return _TRANSIENT
    if unit is None:
        return _TRANSIENT
    try:
        resolved = resolve_attachment(session_id, uow=unit, clock=deps.clock)
    except StorageFailure:
        return _TRANSIENT
    except Exception as exc:
        warn_failure(logger, "queue identity lookup failed", exc)
        return _TRANSIENT
    finally:
        _safe_close(unit)
    if resolved.error_code is not None or resolved.slot_id != deps.attachment.slot_id:
        return None
    if not resolved.slot_id or not resolved.template_id:
        return None
    return resolved.slot_id, resolved.template_id


def _snapshot(deps: ToolDeps) -> dict[str, Any]:
    catalog_id = str(deps.runtime_state.get("catalog_id") or "")
    if not deps.attachment.session_id:
        return limited_report(
            stage="unattached",
            observation_enabled=deps.observation_enabled,
            catalog_id=catalog_id,
            next_action="attach",
            inbound_observed=bool(deps.runtime_state.get("inbound_observed")),
            protocol=_protocol(deps),
        )
    unit, failure = open_unit(deps, read_only=True)
    if failure is not None or unit is None:
        code = "storage_unavailable" if failure is None else str(failure.get("error_code") or "storage_unavailable")
        return {
            "ok": False,
            "error_code": code,
            "message": "storage is busy" if code == "storage_busy" else "storage is unavailable",
            "stage": "storage",
        }
    try:
        resolved = resolve_attachment(deps.attachment.session_id, uow=unit, clock=deps.clock)
        if resolved.error_code is not None or resolved.slot_id != deps.attachment.slot_id:
            return limited_report(
                stage="session_invalid",
                observation_enabled=deps.observation_enabled,
                catalog_id=catalog_id,
                next_action="attach",
                inbound_observed=bool(deps.runtime_state.get("inbound_observed")),
                protocol=_protocol(deps),
            )
        dashboard = load_dashboard(
            uow=unit,
            ids=deps.ids,
            clock=deps.clock,
            viewer_slot_id=resolved.slot_id,
            heartbeat_stale_seconds=deps.heartbeat_stale_seconds,
        )
        tickets = [
            {
                "ticket_id": ticket.ticket_id,
                "state": ticket.state,
                "queue": ticket.queue,
                "target_slot_id": ticket.target_slot_id,
                "claimant": ticket.claimant,
                "review_result": ticket.review_result,
            }
            for ticket in dashboard.tickets
        ]
        if resolved.template_id not in {"mentor", "checker"}:
            tickets = [ticket for ticket in tickets if ticket["target_slot_id"] == resolved.slot_id]
        return {
            "ok": True,
            "resource": QUEUE_URI,
            "tickets": tickets,
            "notice": "A notification means this visible snapshot changed. It does not mean a ticket was claimed or executed.",
        }
    except StorageFailure as exc:
        return {
            "ok": False,
            "error_code": "storage_busy" if exc.kind == "busy" else "storage_unavailable",
            "message": "storage is busy" if exc.kind == "busy" else "storage is unavailable",
            "stage": "storage",
        }
    finally:
        _safe_close(unit)


def _contents(body: dict[str, Any]) -> ReadResourceContents:
    return ReadResourceContents(
        content=json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        mime_type="application/json",
    )


def _ping(deps: ToolDeps, server: Server, ok: bool, error_code: str | None) -> None:
    del server
    record = deps.runtime_state.get("record_lifecycle")
    if not callable(record):
        return
    try:
        record(
            kind="ping",
            tool="ping",
            ok=ok,
            error_code=error_code,
            stage="protocol",
            commit_state="not_started",
        )
    except Exception:
        logger.warning("ping event failed")
