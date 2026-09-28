"""Start the local MCP stdio server. This process does not start an agent."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from ling.bootstrap.container import serve
from ling.infrastructure.observability.paths import OutputCollision


def main(argv: list[str] | None = None) -> int:
    """Read the database path and serve MCP. Logs and usage stay on stderr."""

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="ling", add_help=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)
    if args.help:
        print(
            "usage: python -m ling --database PATH\n"
            "       python -m ling.runtime --database PATH --event-log LOG --state-file STATE\n"
            "       python -m ling.diagnostics --database PATH --event-log LOG --state-file STATE\n"
            "       LING_DATABASE may supply the MCP database path.",
            file=sys.stderr,
        )
        return 0
    database = args.database if isinstance(args.database, str) and args.database.strip() else None
    if database is None:
        environ = os.environ.get("LING_DATABASE", "")
        database = environ.strip() or None
    if database is None:
        print("ling: set --database or LING_DATABASE", file=sys.stderr)
        return 2
    try:
        serve(database)
    except OutputCollision as exc:
        print(f"ling: {exc}", file=sys.stderr)
        return 2
    except Exception:
        logging.getLogger(__name__).warning("ling server stopped")
        print("ling: server stopped", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
