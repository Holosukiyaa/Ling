"""Read-only diagnostics command. It does not start MCP or change the database."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from ling.application.inspection import inspect_snapshot
from ling.application.ports.storage import StorageFailure
from ling.application.queries.dashboard import execute as load_dashboard
from ling.application.queries.diagnostics import project_report, public_log_issues
from ling.bootstrap.runtime import (
    SystemClock,
    UuidIdGenerator,
    heartbeat_stale_seconds,
    progress_stale_seconds,
    request_timeout_seconds,
)
from ling.infrastructure.observability import configured_backups, read_state
from ling.infrastructure.observability.jsonl_reader import read_event_log
from ling.infrastructure.persistence.sqlite.unit_of_work import SqliteDatabase
from ling.interfaces.mcp.server import catalog_identifier

_USAGE = (
    "usage: python -m ling.diagnostics --database PATH "
    "[--event-log LOG] [--state-file STATE] [--request-id ID]"
)


def main(argv: list[str] | None = None) -> int:
    """Print one JSON report on stdout. Errors stay on stderr."""

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="ling.diagnostics", add_help=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--database", default=None)
    parser.add_argument("--event-log", default=None)
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--request-id", default=None)
    args = parser.parse_args(argv)
    if args.help:
        print(_USAGE, file=sys.stderr)
        return 0
    if not isinstance(args.database, str) or not args.database.strip():
        print("ling: diagnostics requires --database", file=sys.stderr)
        return 2
    try:
        database = SqliteDatabase.open_existing_readonly(args.database.strip())
    except StorageFailure:
        print("ling: database is unavailable", file=sys.stderr)
        return 1
    event_log = args.event_log.strip() if isinstance(args.event_log, str) and args.event_log.strip() else ""
    state_file = args.state_file.strip() if isinstance(args.state_file, str) and args.state_file.strip() else ""
    try:
        report = _report(
            database,
            event_log,
            state_file,
            args.request_id if isinstance(args.request_id, str) and args.request_id.strip() else None,
        )
    except StorageFailure:
        print("ling: database is unavailable", file=sys.stderr)
        return 1
    except Exception:
        logging.getLogger(__name__).warning("diagnostics failed")
        print("ling: diagnostics failed", file=sys.stderr)
        return 1
    finally:
        database.close()
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")), file=sys.stdout)
    return 0


def _report(
    database: SqliteDatabase,
    event_log: str,
    state_file: str,
    request_id: str | None,
) -> dict[str, object]:
    clock = SystemClock()
    unit = database.read_unit_of_work()
    try:
        dashboard = load_dashboard(
            uow=unit,
            ids=UuidIdGenerator(),
            clock=clock,
            heartbeat_stale_seconds=heartbeat_stale_seconds(os.environ.get("LING_HEARTBEAT_STALE_SECONDS")),
        )
        events: tuple[dict[str, object], ...] = ()
        issues: tuple[dict[str, str], ...] = ()
        if event_log:
            try:
                loaded = read_event_log(Path(event_log), backups=configured_backups())
            except Exception:
                logging.getLogger(__name__).warning("diagnostics event log read failed")
                issues = public_log_issues(({"code": "event_log_unreadable", "reason": "read_failed"},))
            else:
                events = loaded.events
                issues = public_log_issues(loaded.issues)
        inspector = read_state(Path(state_file)) if state_file else None
        snapshot = inspect_snapshot(
            now=clock.now(),
            slots=_slots(dashboard),
            tickets=_tickets(dashboard),
            events=events,
            observation_enabled=bool(event_log),
            log_issues=issues,
            progress_stale_seconds=progress_stale_seconds(os.environ.get("LING_PROGRESS_STALE_SECONDS")),
            request_timeout_seconds=request_timeout_seconds(os.environ.get("LING_REQUEST_TIMEOUT_SECONDS")),
            inspector_state=inspector,
        )
        return project_report(
            snapshot,
            template_id="mentor",
            slot_id=None,
            catalog_id=catalog_identifier(),
            request_id=request_id,
            run_mode="diagnostics_cli",
        )
    finally:
        unit.close()


def _slots(dashboard: object) -> tuple[dict[str, object], ...]:
    rows = []
    for slot in getattr(dashboard, "slots", ()):
        last = getattr(slot, "last_heartbeat_at", None)
        rows.append(
            {
                "slot_id": slot.slot_id,
                "template_id": slot.template_id,
                "online": slot.online,
                "presence": slot.presence,
                "heartbeat_age_seconds": slot.heartbeat_age_seconds,
                "last_heartbeat_at": None if last is None else last.isoformat(),
            }
        )
    return tuple(rows)


def _tickets(dashboard: object) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "ticket_id": ticket.ticket_id,
            "state": ticket.state,
            "queue": ticket.queue,
            "target_slot_id": ticket.target_slot_id,
            "claimant": ticket.claimant,
            "review_result": ticket.review_result,
        }
        for ticket in getattr(dashboard, "tickets", ())
    )


if __name__ == "__main__":
    raise SystemExit(main())
