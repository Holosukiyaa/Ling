"""Wire SQLite, the local clock, and the MCP stdio server."""

from __future__ import annotations

from pathlib import Path

import anyio

from ling.bootstrap.runtime import SystemClock, UuidIdGenerator
from ling.infrastructure.coordinator import observer_from_environ
from ling.infrastructure.persistence.sqlite import SqliteDatabase
from ling.interfaces.mcp.adapter import ToolDeps
from ling.interfaces.mcp.server import build_server, run_stdio


def compose(database_path: str | Path) -> SqliteDatabase:
    """Open a Ling database file and ensure its schema exists."""

    return SqliteDatabase(database_path)


def serve(database_path: str | Path) -> None:
    """Serve the local MCP tools on stdio until the client disconnects."""

    database = compose(database_path)
    server = build_server(
        ToolDeps(
            open_unit_of_work=database.unit_of_work,
            ids=UuidIdGenerator(),
            clock=SystemClock(),
            observer=observer_from_environ(),
        )
    )
    try:
        anyio.run(run_stdio, server)
    finally:
        database.close()
