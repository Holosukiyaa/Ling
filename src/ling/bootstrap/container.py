"""Wire SQLite, the local clock, and the MCP stdio server."""

from __future__ import annotations

import logging
from pathlib import Path

import anyio

from ling.bootstrap.runtime import SystemClock, UuidIdGenerator
from ling.infrastructure.coordinator import observer_from_environ
from ling.infrastructure.observability import sink_from_environ
from ling.infrastructure.persistence.sqlite import SqliteDatabase
from ling.interfaces.mcp.adapter import ToolDeps
from ling.interfaces.mcp.server import build_server, run_stdio


def compose(database_path: str | Path) -> SqliteDatabase:
    """Open a Ling database file and ensure its schema exists."""

    return SqliteDatabase(database_path)


def serve(database_path: str | Path) -> None:
    """Serve the local MCP tools on stdio until the client disconnects."""

    database = compose(database_path)
    observer = observer_from_environ()
    event_sink = sink_from_environ()
    server = build_server(
        ToolDeps(
            open_unit_of_work=database.unit_of_work,
            ids=UuidIdGenerator(),
            clock=SystemClock(),
            observer=observer,
            event_sink=event_sink,
        )
    )
    try:
        anyio.run(run_stdio, server)
    finally:
        try:
            _close_optional(event_sink, "runtime event sink close failed")
        finally:
            try:
                _close_observer(observer)
            finally:
                database.close()


def _close_observer(observer: object) -> None:
    """Stop background projection. This does not touch the Ling database."""

    _close_optional(observer, "coordinator observer close failed")


def _close_optional(resource: object, message: str) -> None:
    """Call close when the object has one. The application port does not require it."""

    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logging.getLogger(__name__).warning(message, exc_info=True)
