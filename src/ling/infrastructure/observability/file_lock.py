"""Exclusive lock for one sidecar file. Standard library only."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


class InterprocessFileLock:
    """Block until this process holds `<path>` exclusively, then release it."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def acquire(self) -> int:
        """Create the lock file if needed and return the locked descriptor."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(self._path), os.O_CREAT | os.O_RDWR)
        try:
            _lock(descriptor)
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    def release(self, descriptor: int) -> None:
        """Unlock and close. Closing still runs when unlock fails."""

        try:
            _unlock(descriptor)
        finally:
            os.close(descriptor)


def _lock(descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        _ensure_lock_byte(descriptor)
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.001)
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _ensure_lock_byte(descriptor: int) -> None:
    """Windows locks a byte range, so the lock file needs one byte."""

    if os.lseek(descriptor, 0, os.SEEK_END) == 0:
        os.write(descriptor, b"\0")
    os.lseek(descriptor, 0, os.SEEK_SET)
