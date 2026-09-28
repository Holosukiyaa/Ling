"""Persistence failures. Callers see these instead of sqlite3 errors."""

from __future__ import annotations

from ling.application.ports.storage import StorageFailure


class StorageError(StorageFailure):
    """A SQLite operation failed. An open write transaction is rolled back."""


class DuplicateRecord(StorageError):
    """A primary or unique key is already stored. The existing row is unchanged."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="failed", phase="storage")


def storage_kind(exc: BaseException) -> str:
    """Classify a driver error without keeping its text."""

    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return "busy"
    return "unavailable"
