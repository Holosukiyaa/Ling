"""SQLite connections for the Ling persistence boundary."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ling.infrastructure.persistence.sqlite.errors import StorageError, storage_kind


def _timeout_seconds() -> float:
    """Busy wait for a writer lock. The default stays short and bounded."""

    raw = os.environ.get("LING_SQLITE_TIMEOUT_SECONDS", "")
    text = raw.strip()
    if not text:
        return 5.0
    try:
        value = float(text)
    except ValueError:
        return 5.0
    if value <= 0:
        return 5.0
    return value


def connect(path: str | Path) -> sqlite3.Connection:
    """Open one file database for a unit of work.

    WAL is required so a committed unit of work can be read on another
    connection while the next short write is starting. A pure memory database
    cannot honor that mode, so the path must be a file. `foreign_keys` is
    per connection and is enabled here before any read or write.
    """

    location = Path(path)
    if location.name == "" or str(location) == ":memory:":
        raise StorageError("sqlite persistence requires a file path so WAL can be enabled", kind="unavailable", phase="begin")
    try:
        connection = sqlite3.connect(location, timeout=_timeout_seconds(), isolation_level=None)
    except sqlite3.Error as exc:
        raise StorageError("sqlite database is unavailable", kind=storage_kind(exc), phase="begin") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    except sqlite3.Error as exc:
        connection.close()
        raise StorageError("sqlite database is unavailable", kind=storage_kind(exc), phase="begin") from exc
    journal = "" if mode is None else str(mode[0]).lower()
    if journal != "wal":
        connection.close()
        raise StorageError("sqlite database is unavailable", kind="unavailable", phase="begin")
    return connection


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing file without creating it or changing its journal mode."""

    location = Path(path)
    if not location.is_file():
        raise StorageError("sqlite database is unavailable", kind="unavailable", phase="begin")
    uri = f"{location.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, timeout=_timeout_seconds(), isolation_level=None, uri=True)
    except sqlite3.Error as exc:
        raise StorageError("sqlite database is unavailable", kind=storage_kind(exc), phase="begin") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        connection.close()
        raise StorageError("sqlite database is unavailable", kind=storage_kind(exc), phase="begin") from exc
    return connection
