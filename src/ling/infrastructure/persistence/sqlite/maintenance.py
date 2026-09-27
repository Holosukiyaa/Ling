"""Explicit removal of old operation receipts. Business rows are not touched."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ling.infrastructure.persistence.sqlite.errors import StorageError


def purge_operation_receipts(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete receipts created before `cutoff`. One transaction, or no change."""

    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise StorageError("cutoff must include a timezone")
    moment = cutoff.astimezone(timezone.utc).isoformat()
    connection.execute("BEGIN IMMEDIATE")
    try:
        cursor = connection.execute(
            "DELETE FROM operation_receipts WHERE created_at < ?",
            (moment,),
        )
        deleted = int(cursor.rowcount)
        _before_commit(connection)
        connection.execute("COMMIT")
        return deleted
    except Exception:
        _rollback(connection)
        raise


def _before_commit(connection: sqlite3.Connection) -> None:
    """Commit barrier. A failure here rolls the delete back."""

    return


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return
