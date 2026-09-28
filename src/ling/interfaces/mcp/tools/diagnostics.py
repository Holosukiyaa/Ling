"""MCP adapter for the read-only diagnostics report."""

from __future__ import annotations

import logging
from typing import Any

from ling.application.inspection import inspect_snapshot
from ling.application.ports.storage import StorageFailure
from ling.application.queries.dashboard import execute as load_dashboard
from ling.application.queries.diagnostics import limited_report, project_report, public_log_issues
from ling.application.queries.resolve_attachment import execute as resolve_attachment
from ling.interfaces.mcp.adapter import (
    ToolDeps,
    _internal_payload,
    _safe_close,
    _storage_payload,
    open_unit,
    validated,
)
from ling.interfaces.mcp.request_context import warn_failure
from ling.interfaces.mcp.schemas import DiagnosticsInput

logger = logging.getLogger(__name__)


def handle(arguments: dict[str, Any], deps: ToolDeps) -> dict[str, Any]:
    """Return a redacted report. This path does not stage or commit business rows."""

    parsed, error = validated(DiagnosticsInput, arguments, deps)
    if error is not None:
        return error
    request_id = None if parsed is None else getattr(parsed, "request_id", None)
    catalog_id = str(deps.runtime_state.get("catalog_id") or "")
    protocol = _protocol(deps)
    inbound = bool(deps.runtime_state.get("inbound_observed"))
    if not deps.attachment.session_id:
        return _ready(deps, limited_report(
            stage="unattached",
            observation_enabled=deps.observation_enabled,
            catalog_id=catalog_id,
            next_action="attach",
            inbound_observed=inbound,
            protocol=protocol,
        ))
    unit, failure = open_unit(deps, read_only=True)
    if failure is not None or unit is None:
        return failure or _internal_payload(deps, "not_started")
    try:
        resolved = resolve_attachment(deps.attachment.session_id, uow=unit, clock=deps.clock)
        if resolved.error_code is not None or resolved.slot_id != deps.attachment.slot_id:
            return _ready(deps, limited_report(
                stage="session_invalid",
                observation_enabled=deps.observation_enabled,
                catalog_id=catalog_id,
                next_action="attach",
                inbound_observed=inbound,
                protocol=protocol,
            ))
        dashboard = load_dashboard(
            uow=unit,
            ids=deps.ids,
            clock=deps.clock,
            viewer_slot_id=resolved.slot_id,
            heartbeat_stale_seconds=deps.heartbeat_stale_seconds,
        )
        events, issues = _observation(deps)
        snapshot = inspect_snapshot(
            now=deps.clock.now(),
            slots=_slots(dashboard),
            tickets=_tickets(dashboard),
            events=events,
            log_issues=issues,
            observation_enabled=deps.observation_enabled,
            progress_stale_seconds=deps.progress_stale_seconds,
            request_timeout_seconds=deps.request_timeout_seconds,
            inspector_state=_inspector_state(deps),
            viewer_instance_id=deps.runtime_instance_id or None,
            connection_fresh_seconds=max(deps.ping_interval_seconds * 3, deps.ping_timeout_seconds),
        )
        report = project_report(
            snapshot,
            template_id=resolved.template_id,
            slot_id=resolved.slot_id,
            catalog_id=catalog_id,
            request_id=request_id if isinstance(request_id, str) else None,
            memory=tuple(deps.recent_requests),
            protocol=protocol,
        )
        return _ready(deps, report)
    except StorageFailure as exc:
        return _storage_payload(deps, exc)
    except Exception as exc:
        warn_failure(logger, "diagnostics failed", exc)
        return _internal_payload(deps, "unknown")
    finally:
        _safe_close(unit)


def _protocol(deps: ToolDeps) -> str:
    value = deps.runtime_state.get("protocol")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _ready(deps: ToolDeps, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "operation_id": deps.ids.new_operation_id(),
        "ticket_id": None,
        "state": None,
        "queue": None,
        "error_code": None,
        "message": "diagnostics",
        "report": report,
        "_commit_state": "not_started",
    }


def _slots(dashboard: object) -> tuple[dict[str, Any], ...]:
    rows = []
    for slot in getattr(dashboard, "slots", ()):
        last = getattr(slot, "last_heartbeat_at", None)
        rows.append(
            {
                "slot_id": slot.slot_id,
                "template_id": slot.template_id,
                "online": slot.online,
                "presence": slot.presence,
                "heartbeat_age_seconds": slot.heartbeat_age_seconds,
                "last_heartbeat_at": None if last is None else last.isoformat(),
            }
        )
    return tuple(rows)


def _tickets(dashboard: object) -> tuple[dict[str, Any], ...]:
    rows = []
    for ticket in getattr(dashboard, "tickets", ()):
        rows.append(
            {
                "ticket_id": ticket.ticket_id,
                "state": ticket.state,
                "queue": ticket.queue,
                "target_slot_id": ticket.target_slot_id,
                "claimant": ticket.claimant,
                "review_result": ticket.review_result,
            }
        )
    return tuple(rows)


def _observation(deps: ToolDeps) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, str], ...]]:
    """Return validated events and public log issues. A read failure is not an empty log."""

    try:
        found = deps.read_events()
    except Exception as exc:
        warn_failure(logger, "runtime event log read failed", exc)
        return (), public_log_issues(({"code": "event_log_unreadable", "reason": "read_failed"},))
    events_raw: object = ()
    issues_raw: object = ()
    if (
        isinstance(found, tuple)
        and len(found) == 2
        and isinstance(found[0], tuple)
        and isinstance(found[1], tuple)
    ):
        events_raw, issues_raw = found
    elif isinstance(found, tuple):
        events_raw = found
    events = tuple(item for item in events_raw if isinstance(item, dict))
    return events, public_log_issues(issues_raw)


def _inspector_state(deps: ToolDeps) -> dict[str, Any] | None:
    reader = deps.runtime_state.get("read_inspector_state")
    if not callable(reader):
        return None
    try:
        state = reader()
    except Exception as exc:
        warn_failure(logger, "inspector state read failed", exc)
        return None
    if isinstance(state, dict):
        return state
    return None
