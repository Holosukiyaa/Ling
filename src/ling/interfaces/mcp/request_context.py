"""Per-call request id for redacted logs.

The id lives in a ContextVar, not a process-wide variable. anyio copies that
context into a worker thread, and reset drops it before the next call.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_REQUEST_ID: ContextVar[str | None] = ContextVar("ling_mcp_request_id", default=None)


def current_request_id() -> str | None:
    """Return this call's id, or None outside a request."""

    value = _REQUEST_ID.get()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@contextmanager
def bind_request_id(request_id: str | None) -> Iterator[None]:
    """Bind one request id for the current context and restore the previous value."""

    text = request_id.strip() if isinstance(request_id, str) else ""
    token = _REQUEST_ID.set(text or None)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def warn_failure(logger: logging.Logger, message: str, exc: BaseException | None = None) -> None:
    """Log a failure type. Include the request id only when this call has one."""

    request_id = current_request_id()
    if exc is None:
        if request_id is None:
            logger.warning("%s", message)
        else:
            logger.warning("%s request_id=%s", message, request_id)
        return
    kind = type(exc).__name__
    if request_id is None:
        logger.warning("%s type=%s", message, kind)
    else:
        logger.warning("%s request_id=%s type=%s", message, request_id, kind)
