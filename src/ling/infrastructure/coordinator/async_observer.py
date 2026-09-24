"""Queue coordinator projection on one daemon thread.

`observe` returns as soon as the call is queued. HTTP runs later, and a full
queue drops the new call instead of blocking the caller.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_LIMIT = 32
_POLL_SECONDS = 0.05
_PROJECTABLE = frozenset(
    {
        "ling_register_slot",
        "ling_heartbeat",
        "ling_dispatch",
        "ling_claim",
        "ling_abandon_claim",
        "ling_submit",
        "ling_review",
        "ling_consume",
        "ling_acquire_file_lock",
    }
)


class AsyncRuntimeObserver:
    """One background worker in front of the HTTP observer."""

    def __init__(self, inner: object, *, limit: int = QUEUE_LIMIT) -> None:
        self._inner = inner
        self._queue: queue.Queue[tuple[str, dict[str, Any], dict[str, Any]]] = queue.Queue(maxsize=limit)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="ling-coordinator-observer",
            daemon=True,
        )
        self._thread.start()

    def observe(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        """Queue one finished call, or drop it when the queue is full."""

        if self._stop.is_set() or not _accept(tool_name, result):
            return
        try:
            self._queue.put_nowait((tool_name, _plain(arguments), _plain(result)))
        except queue.Full:
            logger.warning("coordinator observation dropped because the queue is full")

    def close(self) -> None:
        """Stop the worker without waiting for HTTP. Unsent calls are abandoned."""

        self._stop.set()
        self._thread.join(timeout=0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                tool_name, arguments, result = self._queue.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                continue
            if self._stop.is_set():
                return
            try:
                observe = getattr(self._inner, "observe")
                observe(tool_name, arguments, result)
            except Exception:
                logger.warning("coordinator observation failed", exc_info=True)


def _accept(tool_name: str, result: object) -> bool:
    if tool_name not in _PROJECTABLE:
        return False
    if not isinstance(result, Mapping) or not result.get("ok"):
        return False
    return True


def _plain(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}
