"""Refuse outputs that alias the database or each other.

The check is read-only. It does not create, replace, or truncate any file.
"""

from __future__ import annotations

import os
from pathlib import Path

_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class OutputCollision(RuntimeError):
    """A planned log, state, lock, temporary, or rotation path is not safe to write."""


def collision_reason(
    database: Path,
    *,
    event_log: Path | None = None,
    state_file: Path | None = None,
    rotation_backups: int = 0,
) -> str | None:
    """Return a label-only reason when two planned paths are the same file."""

    protected = [("database", database)]
    for suffix in _SIDECAR_SUFFIXES:
        protected.append((f"database{suffix}", Path(f"{database}{suffix}")))
    writes: list[tuple[str, Path]] = []
    if event_log is not None:
        writes.append(("event-log", event_log))
        writes.append(("event-log.lock", Path(f"{event_log}.lock")))
        count = rotation_backups if isinstance(rotation_backups, int) and rotation_backups > 0 else 0
        for index in range(1, count + 1):
            writes.append((f"event-log.{index}", Path(f"{event_log}.{index}")))
    if state_file is not None:
        writes.append(("state-file", state_file))
        writes.append(("state-file.tmp", _temporary(state_file)))
        writes.append(("state-file.lock", Path(f"{state_file}.lock")))
    identified = [(label, _keys(path)) for label, path in protected + writes]
    protected_labels = {label for label, _path in protected}
    for index, (label, keys) in enumerate(identified):
        if label in protected_labels:
            continue
        for other_label, other_keys in identified[:index]:
            if keys & other_keys:
                return f"{label} collides with {other_label}"
    return None


def _temporary(path: Path) -> Path:
    name = path.name
    if not name:
        return Path(f"{path}.tmp")
    return path.with_name(name + ".tmp")


def _keys(path: Path) -> set[tuple[object, ...]]:
    keys: set[tuple[object, ...]] = set()
    absolute = path if path.is_absolute() else Path.cwd() / path
    keys.add(("path", _text(absolute)))
    try:
        resolved = absolute.resolve(strict=False)
    except OSError:
        resolved = absolute
    keys.add(("path", _text(resolved)))
    for candidate in (absolute, resolved):
        inode = _inode(candidate)
        if inode is not None:
            keys.add(inode)
    return keys


def _text(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _inode(path: Path) -> tuple[str, int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return ("inode", info.st_dev, info.st_ino)
