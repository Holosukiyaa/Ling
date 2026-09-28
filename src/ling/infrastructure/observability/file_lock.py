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

        descriptor = self.acquire_within(None)
        if descriptor is None:
            raise TimeoutError("runtime file lock was not acquired")
        return descriptor

    def acquire_within(self, seconds: float | None) -> int | None:
        """Return the locked descriptor, or None when a finite wait expires."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(self._path), os.O_CREAT | os.O_RDWR)
        deadline = None if seconds is None else time.perf_counter() + seconds
        try:
            while True:
                if _try_lock(descriptor):
                    return descriptor
                if deadline is not None and time.perf_counter() >= deadline:
                    os.close(descriptor)
                    return None
                time.sleep(0.001)
        except Exception:
            os.close(descriptor)
            raise

    def try_acquire(self) -> int | None:
        """Take the lock if it is free. A busy lock returns None and is not overwritten."""

        return self.acquire_within(0)

    def release(self, descriptor: int) -> None:
        """Unlock and close. Closing still runs when unlock fails."""

        try:
            _unlock(descriptor)
        finally:
            os.close(descriptor)


def _try_lock(descriptor: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        _ensure_lock_byte(descriptor)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


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
