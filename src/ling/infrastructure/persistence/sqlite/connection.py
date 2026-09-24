"""SQLite connections for the Ling persistence boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ling.infrastructure.persistence.sqlite.errors import StorageError


def connect(path: str | Path) -> sqlite3.Connection:
    """Open one file database for a unit of work.

    WAL is required so a committed unit of work can be read on another
    connection while the next short write is starting. A pure memory database
    cannot honor that mode, so the path must be a file. `foreign_keys` is
    per connection and is enabled here before any read or write.
    """

    location = Path(path)
    if location.name == "" or str(location) == ":memory:":
        raise StorageError("sqlite persistence requires a file path so WAL can be enabled")
    connection = sqlite3.connect(location, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    journal = "" if mode is None else str(mode[0]).lower()
    if journal != "wal":
        connection.close()
        raise StorageError(f"sqlite journal_mode is {journal or 'unset'}, expected wal")
    return connection
