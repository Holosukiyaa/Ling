"""Persistence failures. Callers see these instead of sqlite3 errors."""

from __future__ import annotations


class StorageError(Exception):
    """A SQLite operation failed. An open write transaction is rolled back."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DuplicateRecord(StorageError):
    """A primary or unique key is already stored. The existing row is unchanged."""
