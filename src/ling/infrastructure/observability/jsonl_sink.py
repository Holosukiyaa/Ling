"""Append-only JSONL file for local runtime events."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ling.application.ports.observability import RuntimeEvent
from ling.infrastructure.observability.file_lock import InterprocessFileLock

logger = logging.getLogger(__name__)


class JsonlRuntimeEventSink:
    """Append one UTF-8 JSON line per event. Write errors stay on stderr."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._thread_lock = threading.Lock()
        self._file_lock = InterprocessFileLock(Path(str(path) + ".lock"))

    def record(self, event: RuntimeEvent) -> None:
        """Append one full line under the process lock, then flush."""

        try:
            line = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded = line.encode("utf-8")
        except Exception:
            logger.warning("runtime event log write failed", exc_info=True)
            return
        with self._thread_lock:
            descriptor = None
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = self._file_lock.acquire()
                with self._path.open("ab", buffering=0) as handle:
                    handle.write(encoded)
                    handle.flush()
            except Exception:
                logger.warning("runtime event log write failed", exc_info=True)
            finally:
                if descriptor is not None:
                    try:
                        self._file_lock.release(descriptor)
                    except Exception:
                        logger.warning("runtime event log unlock failed", exc_info=True)

    def close(self) -> None:
        """Release nothing held past a write. Repeated calls do not raise."""

        try:
            with self._thread_lock:
                return
        except Exception:
            logger.warning("runtime event log close failed", exc_info=True)
