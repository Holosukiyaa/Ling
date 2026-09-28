"""Explain a failed MCP payload without changing a successful business result."""

from __future__ import annotations

from typing import Any

_SECRET_MARKERS = (
    "attachment_token",
    "token_hash",
    "session_id",
    "traceback",
    "sqlite3",
    "begin immediate",
    "begin ",
)

_SPEC: dict[str, dict[str, object]] = {
    "invalid_input": {"stage": "validation", "retryable": False, "next_action": "fix_input", "commit_state": "not_started", "reason_code": "invalid_input"},
    "not_found": {"stage": "execution", "retryable": False, "next_action": "fix_input", "commit_state": "not_committed", "reason_code": "not_found"},
    "attachment_required": {"stage": "auth", "retryable": False, "next_action": "attach", "commit_state": "not_started", "reason_code": "unattached"},
    "attachment_rejected": {"stage": "auth", "retryable": False, "next_action": "attach", "commit_state": "not_started", "reason_code": "attachment_rejected"},
    "forbidden": {"stage": "auth", "retryable": False, "next_action": "use_allowed_tool", "commit_state": "not_started", "reason_code": "forbidden"},
    "lease_required": {"stage": "auth", "retryable": False, "next_action": "check_lease", "commit_state": "not_started", "reason_code": "lease_required"},
    "invalid_transition": {"stage": "execution", "retryable": False, "next_action": "inspect_conflict", "commit_state": "not_committed", "reason_code": "invalid_transition"},
    "already_claimed": {"stage": "execution", "retryable": False, "next_action": "inspect_conflict", "commit_state": "not_committed", "reason_code": "already_claimed"},
    "not_claimant": {"stage": "execution", "retryable": False, "next_action": "inspect_conflict", "commit_state": "not_committed", "reason_code": "not_claimant"},
    "not_issuer": {"stage": "execution", "retryable": False, "next_action": "inspect_conflict", "commit_state": "not_committed", "reason_code": "not_issuer"},
    "consumption_lock_held": {"stage": "execution", "retryable": False, "next_action": "inspect_conflict", "commit_state": "not_committed", "reason_code": "consumption_lock_held"},
    "lock_mismatch": {"stage": "execution", "retryable": False, "next_action": "inspect_conflict", "commit_state": "not_committed", "reason_code": "lock_mismatch"},
    "conflict": {"stage": "execution", "retryable": True, "next_action": "retry_same_operation_id", "commit_state": "not_committed", "reason_code": "conflict"},
    "storage_busy": {"stage": "storage", "retryable": True, "next_action": "retry_same_operation_id", "commit_state": "not_started", "reason_code": "sqlite_busy"},
    "storage_unavailable": {"stage": "storage", "retryable": True, "next_action": "retry_later", "commit_state": "unknown", "reason_code": "database_unavailable"},
    "internal": {"stage": "execution", "retryable": False, "next_action": "read_diagnostics", "commit_state": "unknown", "reason_code": "unexpected"},
}


def diagnose(payload: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    """Add stable diagnostic fields. Underscore keys never leave this function."""

    code = payload.get("error_code")
    if not isinstance(code, str) or code not in _SPEC:
        code = "internal"
    spec = _SPEC[code]
    message = _safe_message(payload.get("message"), code)
    reason = payload.get("_reason_code")
    if not isinstance(reason, str) or not reason.strip():
        reason = _reason(code, message, str(spec["reason_code"]))
    commit_state = payload.get("_commit_state")
    if commit_state not in {"not_started", "not_committed", "committed", "unknown"}:
        commit_state = spec["commit_state"]
    retryable = spec["retryable"]
    next_action = spec["next_action"]
    if code == "conflict" and "different request" in message:
        reason = "operation_mismatch"
        retryable = False
        next_action = "check_receipt_stop_retry"
    body = {key: value for key, value in payload.items() if not str(key).startswith("_")}
    body["error_code"] = code
    body["message"] = message
    body["request_id"] = request_id
    body["reason_code"] = reason
    body["stage"] = spec["stage"]
    body["retryable"] = retryable
    body["next_action"] = next_action
    body["commit_state"] = commit_state
    return body


def present(payload: dict[str, Any], *, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a tool payload into the business body and MCP metadata.

    A successful body keeps its existing fields. Correlation for that attempt
    stays in metadata so a replayed receipt is not rewritten.
    """

    commit_state = payload.get("_commit_state")
    if payload.get("ok"):
        body = {key: value for key, value in payload.items() if not str(key).startswith("_")}
        if commit_state not in {"not_started", "not_committed", "committed", "unknown"}:
            commit_state = "committed"
        return body, {"request_id": request_id, "commit_state": commit_state}
    body = diagnose(payload, request_id=request_id)
    meta = {
        "request_id": request_id,
        "commit_state": body.get("commit_state"),
        "stage": body.get("stage"),
        "reason_code": body.get("reason_code"),
        "retryable": body.get("retryable"),
        "next_action": body.get("next_action"),
    }
    return body, meta


def _reason(code: str, message: str, default: str) -> str:
    if code == "forbidden":
        if "does not match" in message:
            return "actor_mismatch"
        if "cannot call this tool" in message:
            return "role_forbidden"
        return "forbidden"
    if code == "not_found" and message == "unknown tool":
        return "unknown_tool"
    return default


def _safe_message(message: object, code: str) -> str:
    fallback = "request failed" if code == "internal" else "request was rejected"
    if not isinstance(message, str) or not message.strip():
        return fallback
    lowered = message.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return fallback
    if ":\\" in message or ":/" in message or "\\\\" in message:
        return fallback
    if len(message) > 300:
        return fallback
    return message
