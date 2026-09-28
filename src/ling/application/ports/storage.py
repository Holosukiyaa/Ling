"""Storage failures the application can classify without a database driver."""

from __future__ import annotations


class StorageFailure(Exception):
    """A persistence operation failed. The message must stay free of paths and SQL."""

    def __init__(self, message: str, *, kind: str = "unavailable", phase: str = "storage") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind if kind in {"busy", "unavailable", "failed"} else "unavailable"
        self.phase = phase if phase in {"begin", "commit", "storage"} else "storage"
