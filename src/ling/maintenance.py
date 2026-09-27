"""Delete old operation receipts. This process does not start MCP."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from ling.infrastructure.persistence.sqlite.connection import connect
from ling.infrastructure.persistence.sqlite.errors import StorageError
from ling.infrastructure.persistence.sqlite.maintenance import purge_operation_receipts
from ling.infrastructure.persistence.sqlite.schema import initialize


def main(argv: list[str] | None = None) -> int:
    """Purge receipts older than `--before`. Success text stays on stdout."""

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="ling.maintenance", add_help=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--database", default=None)
    parser.add_argument("--before", default=None)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print(
            "usage: python -m ling.maintenance --database PATH --before ISO_TIMESTAMP\n"
            "       LING_DATABASE may supply the same path.",
            file=sys.stderr,
        )
        return 2
    if args.help:
        print(
            "usage: python -m ling.maintenance --database PATH --before ISO_TIMESTAMP\n"
            "       LING_DATABASE may supply the same path.",
            file=sys.stderr,
        )
        return 0
    database = args.database if isinstance(args.database, str) and args.database.strip() else None
    if database is None:
        database = os.environ.get("LING_DATABASE", "").strip() or None
    if database is None:
        print("ling.maintenance: set --database or LING_DATABASE", file=sys.stderr)
        return 2
    cutoff, error = _cutoff(args.before)
    if error is not None or cutoff is None:
        print(f"ling.maintenance: {error}", file=sys.stderr)
        return 2
    try:
        connection = connect(database)
    except (OSError, StorageError) as exc:
        print(f"ling.maintenance: {exc}", file=sys.stderr)
        return 1
    try:
        try:
            initialize(connection)
            deleted = purge_operation_receipts(connection, cutoff)
        except Exception as exc:
            print(f"ling.maintenance: {exc}", file=sys.stderr)
            return 1
    finally:
        connection.close()
    print(f"deleted={deleted}")
    print(f"cutoff={cutoff.astimezone(timezone.utc).isoformat()}")
    return 0


def _cutoff(value: object) -> tuple[datetime | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, "--before must be a timezone-aware ISO timestamp"
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None, "--before must be a timezone-aware ISO timestamp"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, "--before must include a timezone"
    return parsed, None


if __name__ == "__main__":
    raise SystemExit(main())
