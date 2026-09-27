"""SQLite unit of work. Commit is the only publish point."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from ling.infrastructure.persistence.sqlite.connection import connect
from ling.infrastructure.persistence.sqlite.errors import StorageError
from ling.infrastructure.persistence.sqlite.repositories import (
    SqliteAttachmentSessionRepository,
    SqliteConsumptionLockRepository,
    SqliteFileLockRepository,
    SqliteOperationReceiptRepository,
    SqliteSlotCredentialRepository,
    SqliteSlotRepository,
    SqliteTicketRepository,
    insert_aggregate,
    release_file_lock_rows,
    update_aggregate,
)
from ling.infrastructure.persistence.sqlite.schema import initialize


class SqliteDatabase:
    """One Ling database file. Schema is created on first open."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._units: list[SqliteUnitOfWork] = []
        self._units_lock = threading.Lock()
        with _closing(connect(self.path)) as connection:
            initialize(connection)

    def unit_of_work(self) -> SqliteUnitOfWork:
        """Open a new unit of work on its own connection."""

        unit = SqliteUnitOfWork(connect(self.path), on_close=self._forget)
        with self._units_lock:
            self._units.append(unit)
        return unit

    def close(self) -> None:
        """Close every unit of work still open on this database."""

        with self._units_lock:
            units = list(self._units)
        for unit in units:
            unit.close()

    def __enter__(self) -> SqliteDatabase:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _forget(self, unit: SqliteUnitOfWork) -> None:
        with self._units_lock:
            try:
                self._units.remove(unit)
            except ValueError:
                return


class SqliteUnitOfWork:
    """Stage slots, tickets, and locks, then publish them in one transaction.

    `BEGIN IMMEDIATE` reserves the writer lock for the whole staged set, so
    the ticket row and its consumption lock commit or roll back together.
    Leaving the context by exception rolls the stage back and does not commit.
    A successful block still has to call `commit`; exit never publishes leftovers.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        on_close: Callable[[SqliteUnitOfWork], None] | None = None,
    ) -> None:
        self._conn = connection
        self._on_close = on_close
        self._staged: list[tuple[str, str, object]] = []
        self._loaded: set[tuple[str, str]] = set()
        self._released_file_locks: set[str] = set()
        self._transaction = False
        self.slots = SqliteSlotRepository(self)
        self.tickets = SqliteTicketRepository(self)
        self.consumption_locks = SqliteConsumptionLockRepository(self)
        self.file_locks = SqliteFileLockRepository(self)
        self.operation_receipts = SqliteOperationReceiptRepository(self)
        self.credentials = SqliteSlotCredentialRepository(self)
        self.attachment_sessions = SqliteAttachmentSessionRepository(self)

    def _connection(self) -> sqlite3.Connection:
        """The open connection. Closed units raise `StorageError`."""

        if self._conn is None:
            raise StorageError("unit of work is closed")
        return self._conn

    def staged(self, kind: str, key: str) -> object | None:
        """Return the newest staged aggregate of this identity, if any."""

        for staged_kind, staged_key, item in reversed(self._staged):
            if staged_kind == kind and staged_key == key:
                return item
        return None

    def stage(self, kind: str, key: str, item: object) -> None:
        """Remember `item` until commit. This does not write to SQLite yet."""

        self._require_open()
        self._staged.append((kind, key, item))

    def note_loaded(self, kind: str, key: str) -> None:
        """Remember that `key` already has a row, so a later save updates it."""

        self._loaded.add((kind, key))

    def begin_for_read(self) -> None:
        """Hold the writer lock from the first read through commit or rollback."""

        self._begin()

    def release_file_lock(self, ticket_id: str) -> None:
        """Stage removal of one file lock. A later save of that ticket cancels it."""

        self._released_file_locks.add(ticket_id)
        self._staged = [
            item for item in self._staged if not (item[0] == "file_lock" and item[1] == ticket_id)
        ]

    def file_lock_released(self, ticket_id: str) -> bool:
        """True when this unit of work has staged that ticket's file lock for removal."""

        return ticket_id in self._released_file_locks

    def commit(self) -> None:
        """Write the stage in save order and publish it. Failures roll back."""

        connection = self._require_open()
        written: set[tuple[str, str]] = set()
        self._begin()
        try:
            for kind, key, item in self._staged:
                if kind == "file_lock" and key in self._released_file_locks:
                    continue
                identity = (kind, key)
                if identity in self._loaded or identity in written:
                    update_aggregate(connection, kind, item)
                else:
                    insert_aggregate(connection, kind, item)
                written.add(identity)
            for ticket_id in self._released_file_locks:
                release_file_lock_rows(connection, ticket_id)
            connection.execute("COMMIT")
        except Exception:
            self._rollback_sql()
            raise
        self._transaction = False
        self._loaded.update(written)
        self._loaded.difference_update(("file_lock", ticket_id) for ticket_id in self._released_file_locks)
        self._released_file_locks.clear()
        self._staged.clear()

    def rollback(self) -> None:
        """Drop the stage. Rows published by an earlier commit stay in place."""

        self._staged.clear()
        self._released_file_locks.clear()
        self._rollback_sql()

    def close(self) -> None:
        """Roll back an open write, then close the connection."""

        if self._conn is None:
            return
        self._staged.clear()
        self._released_file_locks.clear()
        self._rollback_sql()
        self._conn.close()
        self._conn = None
        if self._on_close is not None:
            self._on_close(self)

    def __enter__(self) -> SqliteUnitOfWork:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def _require_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError("unit of work is closed")
        return self._conn

    def _begin(self) -> None:
        connection = self._require_open()
        if self._transaction:
            return
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise StorageError("sqlite transaction failed") from exc
        self._transaction = True

    def _rollback_sql(self) -> None:
        if self._conn is None or not self._transaction:
            self._transaction = False
            return
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.Error:
            return
        finally:
            self._transaction = False


class _closing:
    """Close a connection without using sqlite3's commit-on-success context."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.close()
