"""SQLite schema for slots, tickets, locks, and operation receipts.

`operation_receipts` stores one row for each successful mutating command:
operation id, command name, request fingerprint, the original success result,
and `created_at`. A repeated insert of the same slot, ticket, or mentor lock
conflicts on the primary key. Receipts are not domain entities.

The schema version is SQLite's `user_version`. Version 0 means an unstamped
file. Opening it creates any missing current tables and advances to the
current version without changing rows that are already there. Version 2 adds
operation receipts. Version 3 indexes `created_at` for the explicit
maintenance purge. Version 4 adds nullable `tickets.target_slot_id`; existing
rows stay NULL. A newer `user_version` is refused. Each upgrade runs in one
transaction and rolls back when it fails.
"""

from __future__ import annotations

import sqlite3

from ling.infrastructure.persistence.sqlite.errors import StorageError

SCHEMA_VERSION = 4

_VERSION_1_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS slots (
        slot_id TEXT PRIMARY KEY,
        template_id TEXT NOT NULL,
        online INTEGER NOT NULL,
        last_heartbeat_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id TEXT PRIMARY KEY,
        issuer_slot_id TEXT NOT NULL REFERENCES slots(slot_id),
        content TEXT NOT NULL,
        state TEXT NOT NULL,
        claimant_slot_id TEXT REFERENCES slots(slot_id),
        review_result TEXT,
        CHECK (state IN ('queued', 'claimed', 'submitted', 'accepted', 'rejected', 'consumed')),
        CHECK (review_result IS NULL OR review_result IN ('accepted', 'rejected')),
        CHECK (
            (state = 'queued' AND claimant_slot_id IS NULL AND review_result IS NULL)
            OR (state = 'claimed' AND claimant_slot_id IS NOT NULL AND review_result IS NULL)
            OR (state = 'submitted' AND claimant_slot_id IS NOT NULL AND review_result IS NULL)
            OR (state = 'accepted' AND claimant_slot_id IS NOT NULL AND review_result = 'accepted')
            OR (state = 'rejected' AND claimant_slot_id IS NOT NULL AND review_result = 'rejected')
            OR (state = 'consumed' AND claimant_slot_id IS NOT NULL
                AND review_result IN ('accepted', 'rejected'))
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS consumption_locks (
        mentor_slot_id TEXT PRIMARY KEY REFERENCES slots(slot_id),
        ticket_id TEXT UNIQUE REFERENCES tickets(ticket_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS file_locks (
        ticket_id TEXT PRIMARY KEY REFERENCES tickets(ticket_id),
        holder_slot_id TEXT NOT NULL REFERENCES slots(slot_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS file_lock_paths (
        path TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL REFERENCES file_locks(ticket_id)
    )
    """,
)

SCHEMA = ";\n".join(statement.strip() for statement in _VERSION_1_STATEMENTS) + ";"


_VERSION_2_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS operation_receipts (
        operation_id TEXT PRIMARY KEY,
        command_name TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
)


def _upgrade_to_1(connection: sqlite3.Connection) -> None:
    _execute_all(connection, _VERSION_1_STATEMENTS)


_VERSION_3_STATEMENTS = (
    """
    CREATE INDEX IF NOT EXISTS idx_operation_receipts_created_at
        ON operation_receipts (created_at)
    """,
)


def _upgrade_to_2(connection: sqlite3.Connection) -> None:
    _execute_all(connection, _VERSION_2_STATEMENTS)


def _upgrade_to_3(connection: sqlite3.Connection) -> None:
    _execute_all(connection, _VERSION_3_STATEMENTS)


_VERSION_4_STATEMENTS = (
    """
    ALTER TABLE tickets ADD COLUMN target_slot_id TEXT REFERENCES slots(slot_id)
    """,
)


def _upgrade_to_4(connection: sqlite3.Connection) -> None:
    _execute_all(connection, _VERSION_4_STATEMENTS)


_UPGRADES = {1: _upgrade_to_1, 2: _upgrade_to_2, 3: _upgrade_to_3, 4: _upgrade_to_4}


def initialize(connection: sqlite3.Connection) -> None:
    """Create or stamp the current schema. Safe to run again."""

    connection.execute("PRAGMA foreign_keys = ON")
    version = _user_version(connection)
    if version > SCHEMA_VERSION:
        raise StorageError(
            f"sqlite schema version {version} is newer than supported version {SCHEMA_VERSION}"
        )
    if version < 0:
        raise StorageError(f"sqlite schema version {version} is not usable")
    if version == SCHEMA_VERSION:
        return
    _migrate(connection, version)


def _migrate(connection: sqlite3.Connection, version: int) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = version
        while current < SCHEMA_VERSION:
            target = current + 1
            try:
                upgrade = _UPGRADES[target]
            except KeyError as exc:
                raise StorageError(
                    f"sqlite schema has no migration from {current} to {target}"
                ) from exc
            upgrade(connection)
            current = target
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        recorded = _user_version(connection)
        if recorded != SCHEMA_VERSION:
            raise StorageError("sqlite schema version was not recorded")
        connection.execute("COMMIT")
    except Exception as exc:
        _rollback(connection)
        if isinstance(exc, StorageError):
            raise
        if isinstance(exc, sqlite3.Error):
            raise StorageError("sqlite schema migration failed") from exc
        raise


def _execute_all(connection: sqlite3.Connection, statements: tuple[str, ...]) -> None:
    for statement in statements:
        connection.execute(statement)


def _user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise StorageError("sqlite schema version is missing")
    return int(row[0])


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return
