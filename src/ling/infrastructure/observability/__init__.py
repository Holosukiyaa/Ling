"""Optional local runtime-event log. Several Ling processes may share one file."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

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
    return JsonlRuntimeEventSink(Path(raw), max_bytes=_max_bytes(env), backups=_backups(env))


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


__all__ = ["JsonlRuntimeEventSink", "sink_from_environ"]
