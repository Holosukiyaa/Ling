"""Optional local runtime-event log. Ling does not require this file."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ling.application.ports.observability import NullRuntimeEventSink, RuntimeEventSink
from ling.infrastructure.observability.jsonl_sink import JsonlRuntimeEventSink


def sink_from_environ(environ: Mapping[str, str] | None = None) -> RuntimeEventSink:
    """Build a JSONL sink, or a null sink when LING_EVENT_LOG is unset."""

    env = os.environ if environ is None else environ
    raw = str(env.get("LING_EVENT_LOG") or "").strip()
    if not raw:
        return NullRuntimeEventSink()
    return JsonlRuntimeEventSink(Path(raw))


__all__ = ["JsonlRuntimeEventSink", "sink_from_environ"]
