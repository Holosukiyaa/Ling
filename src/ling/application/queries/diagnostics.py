"""Project an inspection snapshot to the caller that is allowed to see it."""

from __future__ import annotations

import hashlib
import json
from typing import Any

GLOBAL_TEMPLATES = frozenset({"mentor", "checker"})
BUILD_VERSION = "0.0.0"
PROTOCOL_UNKNOWN = "unknown"


def queue_token(tickets: list[dict[str, Any]]) -> str:
    """Hash the visible queue identity. Ticket text is not part of the token."""

    rows = sorted(
        (
            ticket.get("ticket_id"),
            ticket.get("state"),
            ticket.get("queue"),
            ticket.get("claimant"),
            ticket.get("target_slot_id"),
            ticket.get("review_result"),
        )
        for ticket in tickets
    )
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def limited_report(
    *,
    stage: str,
    observation_enabled: bool,
    catalog_id: str,
    next_action: str,
    inbound_observed: bool = False,
    protocol: str = PROTOCOL_UNKNOWN,
) -> dict[str, Any]:
    """Return the unattached or invalid-session report. It has no database rows."""

    return {
        "ok": True,
        "stage": stage,
        "build": BUILD_VERSION,
        "protocol": protocol if isinstance(protocol, str) and protocol.strip() else PROTOCOL_UNKNOWN,
        "catalog_id": catalog_id,
        "run_mode": "mcp_stdio",
        "observation_enabled": observation_enabled,
        "observation": "disabled" if not observation_enabled else "enabled",
        "next_action": next_action,
        "host_catalog_refresh": "not_confirmed",
        "inbound_observed": bool(inbound_observed),
        "external_policy_block": "not_observed",
        "note": "No inbound Ling request was matched. That does not prove a client policy block.",
        "request": {"visible": False},
    }


def project_report(
    snapshot: dict[str, Any],
    *,
    template_id: str | None,
    slot_id: str | None,
    catalog_id: str,
    request_id: str | None,
    memory: tuple[dict[str, Any], ...] = (),
    run_mode: str = "mcp_stdio",
    protocol: str = PROTOCOL_UNKNOWN,
) -> dict[str, Any]:
    """Copy the snapshot down to the caller's authority. Operators see the whole report."""

    report = _without_secrets(snapshot)
    if run_mode != "diagnostics_cli" and template_id not in GLOBAL_TEMPLATES:
        report = _own_slot(report, slot_id or "")
    report["requests"] = _merge_memory(report.get("requests") or [], memory, slot_id, template_id, run_mode)
    found = _select_request(report["requests"], request_id)
    report.update(
        {
            "ok": True,
            "stage": "ready",
            "build": BUILD_VERSION,
            "protocol": protocol if isinstance(protocol, str) and protocol.strip() else PROTOCOL_UNKNOWN,
            "catalog_id": catalog_id,
            "run_mode": run_mode,
            "host_catalog_refresh": "not_confirmed",
            "external_policy_block": "not_observed",
            "note": "A client policy block is reported only when the client says so. Silence is not that proof.",
            "request": found,
        }
    )
    return report


def _own_slot(report: dict[str, Any], slot_id: str) -> dict[str, Any]:
    slots = [slot for slot in report.get("slots") or [] if slot.get("slot_id") == slot_id]
    tickets = [ticket for ticket in report.get("tickets") or [] if ticket.get("target_slot_id") == slot_id]
    requests = [item for item in report.get("requests") or [] if item.get("slot_id") == slot_id]
    subjects = {slot_id, *(ticket["ticket_id"] for ticket in tickets)}
    connection = report.get("connection")
    if isinstance(connection, dict) and isinstance(connection.get("instance_id"), str):
        subjects.add(connection["instance_id"])
    alerts = [
        alert
        for alert in report.get("alerts") or []
        if alert.get("subject") in subjects
        or alert.get("code") in {
            "observation_disabled",
            "inspector_stale",
            "inspector_stopped",
            "event_log_rejected_record",
            "event_log_partial_tail",
            "event_log_unreadable",
        }
    ]
    projected = dict(report)
    projected["slots"] = slots
    projected["tickets"] = tickets
    projected["requests"] = requests
    projected["alerts"] = alerts
    return projected


def _merge_memory(
    requests: list[dict[str, Any]],
    memory: tuple[dict[str, Any], ...],
    slot_id: str | None,
    template_id: str | None,
    run_mode: str,
) -> list[dict[str, Any]]:
    seen = {item.get("request_id") for item in requests}
    merged = list(requests)
    for item in memory:
        request_id = item.get("request_id")
        if not isinstance(request_id, str) or request_id in seen:
            continue
        if run_mode != "diagnostics_cli" and template_id not in GLOBAL_TEMPLATES:
            if item.get("slot_id") != slot_id:
                continue
        commit_state = item.get("commit_state") if isinstance(item.get("commit_state"), str) else None
        if commit_state == "unknown" or item.get("ok") not in {True, False}:
            result = "unknown"
        elif item.get("ok") is True:
            result = "ok"
        else:
            result = "failed"
        merged.append(
            {
                "request_id": request_id,
                "tool": item.get("tool"),
                "slot_id": item.get("slot_id"),
                "status": "finished",
                "result": result,
                "start": "known",
                "started_at": item.get("started_at") if isinstance(item.get("started_at"), str) else None,
                "finished_at": item.get("recorded_at") if isinstance(item.get("recorded_at"), str) else None,
                "error_code": item.get("error_code") if isinstance(item.get("error_code"), str) else None,
                "reason_code": item.get("reason_code") if isinstance(item.get("reason_code"), str) else None,
                "stage": item.get("stage") if isinstance(item.get("stage"), str) else None,
                "retryable": item.get("retryable") if isinstance(item.get("retryable"), bool) else None,
                "next_action": item.get("next_action") if isinstance(item.get("next_action"), str) else None,
                "commit_state": commit_state,
                "duration_ms": item.get("duration_ms") if isinstance(item.get("duration_ms"), int) else None,
            }
        )
        seen.add(request_id)
    return merged


_LOG_ISSUE_CODES = frozenset(
    {
        "event_log_rejected_record",
        "event_log_partial_tail",
        "event_log_unreadable",
    }
)
_LOG_ISSUE_REASONS = frozenset(
    {
        "invalid_json",
        "wrong_type",
        "unknown_schema",
        "partial_line",
        "read_failed",
    }
)


def public_log_issues(issues: object) -> tuple[dict[str, str], ...]:
    """Keep only known log-read codes and reasons. Paths and text never pass."""

    if not isinstance(issues, (tuple, list)):
        return ()
    cleaned: list[dict[str, str]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        code = issue.get("code")
        if code not in _LOG_ISSUE_CODES:
            continue
        reason = issue.get("reason")
        if reason not in _LOG_ISSUE_REASONS:
            if code == "event_log_unreadable":
                reason = "read_failed"
            elif code == "event_log_partial_tail":
                reason = "partial_line"
            else:
                reason = "invalid_json"
        cleaned.append({"code": str(code), "reason": str(reason)})
    return tuple(cleaned)


def _select_request(requests: list[dict[str, Any]], request_id: str | None) -> dict[str, Any]:
    if not isinstance(request_id, str) or not request_id.strip():
        return {"visible": False}
    for item in requests:
        if item.get("request_id") == request_id:
            return {"visible": True, **item}
    return {"visible": False}


def _without_secrets(snapshot: dict[str, Any]) -> dict[str, Any]:
    hidden = {"content", "attachment_token", "token_hash", "session_id", "paths", "sql"}
    return _drop(snapshot, hidden)


def _drop(value: Any, hidden: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: _drop(item, hidden) for key, item in value.items() if key not in hidden}
    if isinstance(value, list):
        return [_drop(item, hidden) for item in value]
    if isinstance(value, tuple):
        return [_drop(item, hidden) for item in value]
    return value
