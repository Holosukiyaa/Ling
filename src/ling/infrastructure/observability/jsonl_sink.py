"""Append-only JSONL file for local runtime events."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ling.application.ports.observability import RuntimeEvent

logger = logging.getLogger(__name__)


class JsonlRuntimeEventSink:
    """Append one UTF-8 JSON line per event. Write errors stay on stderr."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._handle: object | None = None

    def record(self, event: RuntimeEvent) -> None:
        """Append the event and flush. A write failure does not raise."""

        try:
            line = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._lock:
                handle = self._open()
                handle.write(line)
                handle.flush()
        except Exception:
            logger.warning("runtime event log write failed", exc_info=True)

    def close(self) -> None:
        """Close the log file. This does not touch Ling business state."""

        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is None:
            return
        try:
            handle.close()
        except Exception:
            logger.warning("runtime event log close failed", exc_info=True)

    def _open(self):
        handle = self._handle
        if handle is not None:
            return handle
        self._path.parent.mkdir(parents=True, exist_ok=True)
        opened = self._path.open("a", encoding="utf-8", newline="\n")
        self._handle = opened
        return opened
