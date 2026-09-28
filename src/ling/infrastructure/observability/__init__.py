"""Optional local runtime-event log. Several Ling processes may share one file."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

import json

from ling.application.ports.observability import NullRuntimeEventSink, RuntimeEventSink
from ling.infrastructure.observability.jsonl_sink import JsonlRuntimeEventSink

logger = logging.getLogger(__name__)
_DEFAULT_BACKUPS = 3


def sink_from_environ(environ: Mapping[str, str] | None = None) -> RuntimeEventSink:
    """Build a JSONL sink, or a null sink when LING_EVENT_LOG is unset."""

    env = os.environ if environ is None else environ
    raw = str(env.get("LING_EVENT_LOG") or "").strip()
    if not raw:
        return NullRuntimeEventSink()
    max_bytes, backups = event_log_policy(env)
    return JsonlRuntimeEventSink(Path(raw), max_bytes=max_bytes, backups=backups)


def event_log_policy(environ: Mapping[str, str] | None = None) -> tuple[int | None, int]:
    """Return the effective rotation limit and backup count.

    An empty max-byte setting means no rotation. Invalid values are warned and
    disabled or replaced, matching the MCP sink.
    """

    env = os.environ if environ is None else environ
    return _max_bytes(env), _backups(env)


def configured_backups(environ: Mapping[str, str] | None = None) -> int:
    """How many rotated backups a reader may open.

    This matches the runtime. No positive size limit means rotation is off, so
    only the live file is evidence. Backup count 0 keeps no renamed files.
    """

    max_bytes, backups = event_log_policy(environ)
    if max_bytes is None or not isinstance(backups, int) or backups <= 0:
        return 0
    return backups


def _max_bytes(env: Mapping[str, str]) -> int | None:
    raw = env.get("LING_EVENT_LOG_MAX_BYTES")
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    if text.isdigit() and int(text) > 0:
        return int(text)
    logger.warning("LING_EVENT_LOG_MAX_BYTES is invalid; rotation disabled")
    return None


def _backups(env: Mapping[str, str]) -> int:
    raw = env.get("LING_EVENT_LOG_BACKUPS")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_BACKUPS
    text = str(raw).strip()
    if text.isdigit():
        return int(text)
    logger.warning("LING_EVENT_LOG_BACKUPS is invalid; using %s", _DEFAULT_BACKUPS)
    return _DEFAULT_BACKUPS


def read_state(path: Path) -> dict[str, object] | None:
    """Return a state file's status fields, or None when it is missing or partial."""

    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        parsed = json.loads(text)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    inspector = parsed.get("inspector")
    source = inspector if isinstance(inspector, dict) else parsed
    status = source.get("status")
    if status not in {"running", "stopped"}:
        return None
    return {
        "status": status,
        "updated_at": source.get("updated_at"),
        "interval_seconds": source.get("interval_seconds"),
        "instance_id": source.get("instance_id") if isinstance(source.get("instance_id"), str) else None,
    }


def write_state(path: Path, payload: dict[str, object]) -> None:
    """Replace the state file atomically. The payload must already be redacted."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


__all__ = [
    "JsonlRuntimeEventSink",
    "configured_backups",
    "event_log_policy",
    "read_state",
    "sink_from_environ",
    "write_state",
]
