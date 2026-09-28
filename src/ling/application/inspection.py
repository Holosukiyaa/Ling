"""Read-only inspection of heartbeats, tickets, and runtime events.

The result is a derived snapshot. It does not release locks, move tickets,
or renew a lease.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ling.application.ports.observability import RUNTIME_EVENT_SCHEMA, RUNTIME_LIFECYCLE_SCHEMA

BUSINESS_PROGRESS_TOOLS = frozenset(
    {
        "ling_dispatch",
        "ling_claim",
        "ling_abandon_claim",
        "ling_submit",
        "ling_review",
        "ling_consume",
    }
)
STALL_STATES = frozenset({"queued", "claimed", "submitted"})
DEFAULT_PROGRESS_STALE_SECONDS = 1800
DEFAULT_REQUEST_TIMEOUT_SECONDS = 1800


def inspect_snapshot(
    *,
    now: datetime,
    slots: tuple[dict[str, Any], ...],
    tickets: tuple[dict[str, Any], ...],
    events: tuple[dict[str, Any], ...],
    observation_enabled: bool,
    progress_stale_seconds: int = DEFAULT_PROGRESS_STALE_SECONDS,
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    inspector_state: dict[str, Any] | None = None,
    viewer_instance_id: str | None = None,
    connection_fresh_seconds: float = 90.0,
    log_issues: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Explain what the records show. Missing evidence stays unknown."""

    progress_limit = _positive(progress_stale_seconds, DEFAULT_PROGRESS_STALE_SECONDS)
    request_limit = _positive(request_timeout_seconds, DEFAULT_REQUEST_TIMEOUT_SECONDS)
    requests = _requests(events, now, request_limit)
    alerts: list[dict[str, str]] = []
    if not observation_enabled:
        alerts.append({"code": "observation_disabled", "subject": "event_log"})
    for issue in log_issues:
        code = issue.get("code") if isinstance(issue, dict) else None
        if code in {"event_log_rejected_record", "event_log_partial_tail", "event_log_unreadable"}:
            alerts.append({"code": str(code), "subject": "event_log"})
    ticket_rows = []
    for ticket in tickets:
        row, ticket_alerts = _ticket_row(ticket, events, now, progress_limit, observation_enabled)
        ticket_rows.append(row)
        alerts.extend(ticket_alerts)
    for request in requests:
        if request["result"] == "unknown" and request["status"] in {"in_progress", "timed_out", "unknown"}:
            alerts.append({"code": "request_result_unknown", "subject": str(request["request_id"])})
        if request["status"] == "timed_out":
            alerts.append({"code": "request_timeout", "subject": str(request["request_id"])})
    inspector = _inspector(inspector_state, now)
    if inspector.get("stale") is True:
        alerts.append({"code": "inspector_stale", "subject": "inspector"})
    if inspector.get("status") == "stopped":
        alerts.append({"code": "inspector_stopped", "subject": "inspector"})
    views = _connections(events, now, _fresh(connection_fresh_seconds))
    connection, visible = _select_connection(views, viewer_instance_id)
    for view in visible:
        if view.get("observed") == "started" and view.get("responsive") is False:
            alerts.append(
                {"code": "connection_unresponsive", "subject": str(view.get("instance_id") or "")}
            )
    for slot in slots:
        presence = slot.get("presence")
        if presence == "stale":
            alerts.append({"code": "slot_stale", "subject": str(slot.get("slot_id") or "")})
        elif presence == "unknown":
            alerts.append({"code": "slot_unknown", "subject": str(slot.get("slot_id") or "")})
    return {
        "schema": "ling.inspection.v1",
        "observation_enabled": observation_enabled,
        "automatic_changes": False,
        "slots": list(slots),
        "tickets": ticket_rows,
        "requests": requests,
        "connection": connection,
        "connections": visible,
        "inspector": inspector,
        "alerts": alerts,
        "limits": {
            "host_catalog_refresh": "not_confirmed",
            "model_attention": "not_observable",
            "external_policy_block": "not_observed",
            "ping_does_not_heartbeat": True,
            "heartbeat_does_not_advance_tickets": True,
        },
    }


def _ticket_row(
    ticket: dict[str, Any],
    events: tuple[dict[str, Any], ...],
    now: datetime,
    progress_limit: int,
    observation_enabled: bool,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    ticket_id = str(ticket.get("ticket_id") or "")
    state = str(ticket.get("state") or "")
    at = _progress_time(ticket_id, state, events)
    alerts: list[dict[str, str]] = []
    progress_at = None if at is None else at.isoformat()
    alert = None
    if state in STALL_STATES:
        if at is None or not observation_enabled:
            alert = "progress_unknown"
            alerts.append({"code": "ticket_progress_unknown", "subject": ticket_id})
        else:
            age = int((now - at).total_seconds())
            if age < 0:
                age = 0
            if age > progress_limit:
                alert = "stalled"
                alerts.append({"code": "ticket_stalled", "subject": ticket_id})
    return (
        {
            "ticket_id": ticket_id,
            "state": state,
            "queue": ticket.get("queue"),
            "target_slot_id": ticket.get("target_slot_id"),
            "claimant": ticket.get("claimant"),
            "review_result": ticket.get("review_result"),
            "progress_at": progress_at,
            "progress": "unknown" if at is None else "known",
            "alert": alert,
        },
        alerts,
    )


def _progress_time(ticket_id: str, state: str, events: tuple[dict[str, Any], ...]) -> datetime | None:
    """Latest first commit whose recorded state is still the ticket state.

    Replays, heartbeats, and events for a different state are not progress.
    Missing or rotated evidence stays unknown instead of borrowing another state.
    """

    if not ticket_id or not state:
        return None
    latest: datetime | None = None
    for event in events:
        if not _is_progress_event(event):
            continue
        if event.get("ticket_id") != ticket_id or event.get("state") != state:
            continue
        recorded = _at(event.get("recorded_at"))
        if recorded is None:
            continue
        if latest is None or recorded >= latest:
            latest = recorded
    return latest


def _is_progress_event(event: dict[str, Any]) -> bool:
    if event.get("replay") is True:
        return False
    tool = event.get("tool")
    if not isinstance(tool, str) or tool not in BUSINESS_PROGRESS_TOOLS or event.get("ok") is not True:
        return False
    schema = event.get("schema")
    if schema == RUNTIME_EVENT_SCHEMA:
        return True
    return schema == RUNTIME_LIFECYCLE_SCHEMA and event.get("kind") == "finished"


def _requests(
    events: tuple[dict[str, Any], ...],
    now: datetime,
    request_limit: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for event in events:
        if event.get("schema") != RUNTIME_LIFECYCLE_SCHEMA:
            continue
        request_id = event.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            continue
        if request_id not in grouped:
            order.append(request_id)
            grouped[request_id] = []
        grouped[request_id].append(event)
    rows: list[dict[str, Any]] = []
    for request_id in order:
        items = grouped[request_id]
        received = next((item for item in items if item.get("kind") == "received"), None)
        finished = None
        for item in items:
            if item.get("kind") == "finished":
                finished = item
        if finished is not None:
            status = "finished"
            commit_state = finished.get("commit_state")
            if commit_state == "unknown" or finished.get("ok") not in {True, False}:
                result = "unknown"
            elif finished.get("ok") is True:
                result = "ok"
            else:
                result = "failed"
        elif received is not None:
            received_at = _at(received.get("recorded_at"))
            if received_at is None:
                status = "unknown"
            else:
                age = (now - received_at).total_seconds()
                status = "timed_out" if age > request_limit else "in_progress"
            result = "unknown"
        else:
            status = "unknown"
            result = "unknown"
        slot_id = None
        for item in items:
            if isinstance(item.get("slot_id"), str):
                slot_id = item["slot_id"]
        tool = None
        for item in items:
            if isinstance(item.get("tool"), str):
                tool = item["tool"]
        fact = finished or {}
        rows.append(
            {
                "request_id": request_id,
                "tool": tool,
                "slot_id": slot_id,
                "status": status,
                "result": result,
                "start": "known" if received is not None else "unknown",
                "started_at": _evidence_time(None if received is None else received.get("recorded_at")),
                "finished_at": _evidence_time(None if finished is None else finished.get("recorded_at")),
                "error_code": fact.get("error_code") if isinstance(fact.get("error_code"), str) else None,
                "reason_code": fact.get("reason_code") if isinstance(fact.get("reason_code"), str) else None,
                "stage": fact.get("stage") if isinstance(fact.get("stage"), str) else None,
                "retryable": fact.get("retryable") if isinstance(fact.get("retryable"), bool) else None,
                "next_action": fact.get("next_action") if isinstance(fact.get("next_action"), str) else None,
                "commit_state": fact.get("commit_state") if isinstance(fact.get("commit_state"), str) else None,
                "duration_ms": fact.get("duration_ms") if isinstance(fact.get("duration_ms"), int) else None,
            }
        )
    return rows


def _connections(
    events: tuple[dict[str, Any], ...],
    now: datetime,
    fresh_seconds: float,
) -> list[dict[str, Any]]:
    """One row per runtime instance. A later event must not rewrite another instance."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for event in events:
        if event.get("schema") != RUNTIME_LIFECYCLE_SCHEMA:
            continue
        instance_id = event.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            instance_id = "unknown"
        if instance_id not in grouped:
            order.append(instance_id)
            grouped[instance_id] = []
        grouped[instance_id].append(event)
    return [_one_connection(instance_id, grouped[instance_id], now, fresh_seconds) for instance_id in order]


def _one_connection(
    instance_id: str,
    events: list[dict[str, Any]],
    now: datetime,
    fresh_seconds: float,
) -> dict[str, Any]:
    phase = "unknown"
    last_ping: dict[str, Any] | None = None
    for event in events:
        kind = event.get("kind")
        if not isinstance(kind, str):
            continue
        if kind in {"service_started", "connection_started"}:
            phase = "started"
        elif kind in {"service_stopped", "connection_stopped"}:
            phase = "stopped"
        elif kind == "ping":
            last_ping = event
    ping_at = None if last_ping is None else _at(last_ping.get("recorded_at"))
    responsive: bool | None = None
    if last_ping is None:
        responsive = None
    elif last_ping.get("ok") is True:
        if ping_at is None:
            responsive = None
        else:
            age = (now - ping_at).total_seconds()
            responsive = age <= fresh_seconds
    elif last_ping.get("ok") is False:
        responsive = False
    if phase == "stopped":
        responsive = False if last_ping is not None else None
    return {
        "instance_id": instance_id,
        "observed": phase,
        "responsive": responsive,
        "ping_at": None if ping_at is None else ping_at.isoformat(),
        "ping_is_heartbeat": False,
        "agent_working": "not_observable",
    }


def _select_connection(
    views: list[dict[str, Any]],
    viewer_instance_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """MCP sees only its instance. A CLI rollup does not let one stop hide another run."""

    if isinstance(viewer_instance_id, str) and viewer_instance_id:
        matched = [view for view in views if view.get("instance_id") == viewer_instance_id]
        chosen = matched[-1] if matched else _blank_connection(viewer_instance_id)
        return chosen, [chosen]
    if not views:
        return _blank_connection(None), []
    started = [view for view in views if view.get("observed") == "started"]
    if started:
        observed = "started"
        if any(view.get("responsive") is False for view in started):
            responsive: bool | None = False
        elif all(view.get("responsive") is True for view in started):
            responsive = True
        else:
            responsive = None
    elif all(view.get("observed") == "stopped" for view in views):
        observed = "stopped"
        responsive = False if any(view.get("responsive") is False for view in views) else None
    else:
        observed = "unknown"
        responsive = None
    if observed == "stopped" and responsive is True:
        responsive = False
    summary = _blank_connection(None)
    summary["observed"] = observed
    summary["responsive"] = responsive
    return summary, views


def _blank_connection(instance_id: str | None) -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "observed": "unknown",
        "responsive": None,
        "ping_at": None,
        "ping_is_heartbeat": False,
        "agent_working": "not_observable",
    }


def _fresh(value: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return 90.0


def _inspector(state: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    if not state:
        return {"status": "unknown", "stale": None, "updated_at": None}
    status = state.get("status")
    if status not in {"running", "stopped"}:
        status = "unknown"
    updated = _at(state.get("updated_at"))
    interval = state.get("interval_seconds")
    try:
        gap = float(interval) if interval is not None else 5.0
    except (TypeError, ValueError):
        gap = 5.0
    if gap <= 0:
        gap = 5.0
    stale = None
    if status == "running":
        if updated is None:
            stale = True
            status = "stale"
        else:
            age = (now - updated).total_seconds()
            stale = age > max(gap * 2, gap + 1)
            if stale:
                status = "stale"
    return {
        "status": status,
        "stale": stale,
        "updated_at": None if updated is None else updated.isoformat(),
        "instance_id": state.get("instance_id") if isinstance(state.get("instance_id"), str) else None,
    }


def _evidence_time(value: object) -> str | None:
    """Return a timestamp only when the log already stored one. Do not invent it."""

    if isinstance(value, str) and value.strip():
        return value
    return None


def _at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _positive(value: int, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default
