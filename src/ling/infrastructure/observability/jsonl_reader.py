"""Read a shared runtime JSONL log without taking the business writer lock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from ling.application.ports.observability import RUNTIME_EVENT_SCHEMA, RUNTIME_LIFECYCLE_SCHEMA

_ALLOWED = frozenset(
    {
        "schema",
        "kind",
        "recorded_at",
        "instance_id",
        "request_id",
        "tool",
        "slot_id",
        "operation_id",
        "ok",
        "error_code",
        "ticket_id",
        "state",
        "queue",
        "stage",
        "commit_state",
        "duration_ms",
        "replay",
        "reason_code",
        "retryable",
        "next_action",
    }
)
_BOOL = frozenset({"ok", "replay", "retryable"})
_INT = frozenset({"queue", "duration_ms"})
_REQUIRED = {
    RUNTIME_EVENT_SCHEMA: ("schema", "recorded_at", "tool", "ok"),
    RUNTIME_LIFECYCLE_SCHEMA: ("schema", "kind", "recorded_at", "instance_id"),
}


class EventRead(NamedTuple):
    """Validated events plus explainable problems. Bad lines are not rewritten."""

    events: tuple[dict[str, object], ...]
    issues: tuple[dict[str, str], ...]


def read_events(path: Path, *, backups: int | None = None) -> tuple[dict[str, object], ...]:
    """Return complete validated events. Prefer `read_event_log` when issues matter."""

    return read_event_log(path, backups=backups).events


def read_event_log(path: Path, *, backups: int | None = None) -> EventRead:
    """Read the live file and the configured backups. One bad record does not drop the rest.

    `backups=None` uses the same effective rotation count as the runtime.
    Only `.1` .. `.N` for that count are opened. Missing names are not an error.
    """

    if backups is None:
        from ling.infrastructure.observability import configured_backups

        backups = configured_backups()
    kept = backups if isinstance(backups, int) and backups > 0 else 0
    files = [Path(f"{path}.{index}") for index in range(kept, 0, -1)]
    files.append(path)
    rows: list[dict[str, object]] = []
    issues: list[dict[str, str]] = []
    for item in files:
        found, problems = _read_one(item)
        rows.extend(found)
        issues.extend(problems)
    return EventRead(tuple(rows), tuple(issues))


def _read_one(path: Path) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    if path.exists() and not path.is_file():
        return [], [{"code": "event_log_unreadable", "reason": "read_failed"}]
    if not path.is_file():
        return [], []
    try:
        raw = path.read_bytes()
    except OSError:
        return [], [{"code": "event_log_unreadable", "reason": "read_failed"}]
    if not raw:
        return [], []
    issues: list[dict[str, str]] = []
    if not raw.endswith(b"\n"):
        head, separator, _tail = raw.rpartition(b"\n")
        issues.append({"code": "event_log_partial_tail", "reason": "partial_line"})
        if separator == b"":
            return [], issues
        raw = head + b"\n"
    text = raw.decode("utf-8", errors="replace")
    found: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            issues.append({"code": "event_log_rejected_record", "reason": "invalid_json"})
            continue
        checked = _validate(item)
        if isinstance(checked, dict):
            found.append(checked)
        else:
            issues.append({"code": "event_log_rejected_record", "reason": checked})
    return found, issues


def _validate(item: object) -> dict[str, object] | str:
    if not isinstance(item, dict):
        return "wrong_type"
    schema = item.get("schema")
    if not isinstance(schema, str) or schema not in _REQUIRED:
        return "unknown_schema"
    cleaned: dict[str, object] = {}
    for key, value in item.items():
        if key not in _ALLOWED:
            continue
        if value is None:
            if key in _REQUIRED[schema]:
                return "wrong_type"
            continue
        if not _typed(key, value):
            return "wrong_type"
        cleaned[key] = value
    for key in _REQUIRED[schema]:
        if key not in cleaned:
            return "wrong_type"
        if isinstance(cleaned[key], str) and not str(cleaned[key]).strip():
            return "wrong_type"
    return cleaned


def _typed(key: str, value: object) -> bool:
    if key in _BOOL:
        return isinstance(value, bool)
    if key in _INT:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str)
