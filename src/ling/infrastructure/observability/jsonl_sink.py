"""Append-only JSONL file for local runtime events."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from ling.application.ports.observability import LifecycleEvent, RuntimeEvent
from ling.infrastructure.observability.file_lock import InterprocessFileLock

logger = logging.getLogger(__name__)


class JsonlRuntimeEventSink:
    """Append one UTF-8 JSON line per event. Write errors stay on stderr.

    Rotation is optional. `max_bytes` None keeps appending forever. A positive
    limit renames the live file to `.1` .. `.backups` before the next line
    would pass that size. One line larger than the limit is still written.
    """

    def __init__(self, path: Path, *, max_bytes: int | None = None, backups: int = 3) -> None:
        self._path = path
        self._max_bytes = max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else None
        self._backups = backups if isinstance(backups, int) and backups >= 0 else 3
        self._thread_lock = threading.Lock()
        self._file_lock = InterprocessFileLock(Path(str(path) + ".lock"))

    def record(self, event: RuntimeEvent | LifecycleEvent) -> None:
        """Append one full line under the process lock, then flush."""

        try:
            line = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded = line.encode("utf-8")
        except Exception:
            logger.warning("runtime event log write failed")
            return
        with self._thread_lock:
            descriptor = None
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = self._file_lock.acquire_within(1)
                if descriptor is None:
                    logger.warning("runtime event log lock timed out")
                    return
                if self._max_bytes is not None and self._should_rotate(len(encoded)):
                    try:
                        self._rotate()
                    except Exception:
                        logger.warning("runtime event log rotation failed")
                with self._path.open("ab", buffering=0) as handle:
                    handle.write(encoded)
                    handle.flush()
            except Exception:
                logger.warning("runtime event log write failed")
            finally:
                if descriptor is not None:
                    try:
                        self._file_lock.release(descriptor)
                    except Exception:
                        logger.warning("runtime event log unlock failed")

    def close(self) -> None:
        """Release nothing held past a write. Repeated calls do not raise."""

        try:
            with self._thread_lock:
                return
        except Exception:
            logger.warning("runtime event log close failed")

    def _should_rotate(self, incoming: int) -> bool:
        if not self._path.is_file():
            return False
        current = self._path.stat().st_size
        if current <= 0:
            return False
        limit = self._max_bytes
        return limit is not None and current + incoming > limit

    def _rotate(self) -> None:
        if self._backups <= 0:
            self._path.unlink()
            return
        oldest = self._backup(self._backups)
        if oldest.exists():
            oldest.unlink()
        for index in range(self._backups - 1, 0, -1):
            source = self._backup(index)
            if source.exists():
                os.replace(source, self._backup(index + 1))
        if self._path.exists():
            os.replace(self._path, self._backup(1))

    def _backup(self, index: int) -> Path:
        return Path(f"{self._path}.{index}")
