"""Foreground runtime inspector. It does not start an agent or install a service."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ling.application.inspection import inspect_snapshot
from ling.application.ports.observability import RUNTIME_LIFECYCLE_SCHEMA, LifecycleEvent
from ling.application.ports.storage import StorageFailure
from ling.application.queries.dashboard import execute as load_dashboard
from ling.bootstrap.runtime import (
    SystemClock,
    UuidIdGenerator,
    heartbeat_stale_seconds,
    new_instance_id,
    positive_seconds,
    progress_stale_seconds,
    request_timeout_seconds,
    DEFAULT_INSPECT_INTERVAL_SECONDS,
)
from ling.infrastructure.observability import event_log_policy, write_state
from ling.infrastructure.observability.jsonl_reader import read_event_log
from ling.infrastructure.observability.jsonl_sink import JsonlRuntimeEventSink
from ling.infrastructure.observability.file_lock import InterprocessFileLock
from ling.infrastructure.observability.paths import collision_reason
from ling.infrastructure.persistence.sqlite.unit_of_work import SqliteDatabase

_USAGE = (
    "usage: python -m ling.runtime --database PATH --event-log LOG --state-file STATE [--interval SECONDS]"
)


def main(argv: list[str] | None = None) -> int:
    """Inspect an existing database until interrupted. Stdout stays empty."""

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="ling.runtime", add_help=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--database", default=None)
    parser.add_argument("--event-log", default=None)
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--interval", default=None)
    args = parser.parse_args(argv)
    if args.help:
        print(_USAGE, file=sys.stderr)
        return 0
    database_path = _required(args.database)
    event_log = _required(args.event_log)
    state_file = _required(args.state_file)
    if database_path is None or event_log is None or state_file is None:
        print("ling: inspector requires --database, --event-log, and --state-file", file=sys.stderr)
        return 2
    interval = positive_seconds(args.interval, DEFAULT_INSPECT_INTERVAL_SECONDS)
    if args.interval is not None and positive_seconds(args.interval, -1) <= 0:
        print("ling: interval must be a positive number of seconds", file=sys.stderr)
        return 2
    max_bytes, backups = event_log_policy()
    rotating = backups if max_bytes is not None else 0
    conflict = collision_reason(
        Path(database_path),
        event_log=Path(event_log),
        state_file=Path(state_file),
        rotation_backups=rotating,
    )
    if conflict is not None:
        print(f"ling: {conflict}", file=sys.stderr)
        return 2
    try:
        database = SqliteDatabase.open_existing_readonly(database_path)
    except StorageFailure:
        print("ling: database is unavailable", file=sys.stderr)
        return 1
    lock = InterprocessFileLock(Path(f"{state_file}.lock"))
    descriptor = lock.try_acquire()
    if descriptor is None:
        database.close()
        print("ling: inspector is already running", file=sys.stderr)
        return 1
    sink = JsonlRuntimeEventSink(Path(event_log), max_bytes=max_bytes, backups=backups)
    instance_id = new_instance_id()
    clock = SystemClock()
    ids = UuidIdGenerator()
    try:
        _publish(
            database,
            ids,
            clock,
            Path(event_log),
            Path(state_file),
            sink,
            instance_id,
            interval,
            "running",
            rotating,
        )
        while True:
            time.sleep(interval)
            _publish(
                database,
                ids,
                clock,
                Path(event_log),
                Path(state_file),
                sink,
                instance_id,
                interval,
                "running",
                rotating,
            )
    except KeyboardInterrupt:
        _publish(
            database,
            ids,
            clock,
            Path(event_log),
            Path(state_file),
            sink,
            instance_id,
            interval,
            "stopped",
            rotating,
        )
        return 0
    except Exception:
        logging.getLogger(__name__).warning("inspector tick failed")
        print("ling: inspector stopped", file=sys.stderr)
        return 1
    finally:
        try:
            lock.release(descriptor)
        finally:
            try:
                sink.close()
            finally:
                database.close()


def _required(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _publish(
    database: SqliteDatabase,
    ids: UuidIdGenerator,
    clock: SystemClock,
    event_log: Path,
    state_file: Path,
    sink: JsonlRuntimeEventSink,
    instance_id: str,
    interval: float,
    status: str,
    rotation_backups: int,
) -> None:
    now = clock.now()
    snapshot = _snapshot(database, ids, clock, event_log, now, rotation_backups)
    updated_at = datetime.now(timezone.utc).isoformat()
    inspector = {
        "status": status,
        "updated_at": updated_at,
        "interval_seconds": interval,
        "instance_id": instance_id,
    }
    payload = {
        "schema": "ling.runtime_state.v1",
        "status": status,
        "updated_at": updated_at,
        "interval_seconds": interval,
        "instance_id": instance_id,
        "inspector": inspector,
        "snapshot": snapshot,
    }
    try:
        write_state(state_file, payload)
    except OSError:
        logging.getLogger(__name__).warning("inspector state write failed")
    sink.record(
        LifecycleEvent(
            schema=RUNTIME_LIFECYCLE_SCHEMA,
            kind="tick",
            recorded_at=updated_at,
            instance_id=instance_id,
            tool="ling.runtime",
            ok=True,
            stage="inspection",
            commit_state="not_started",
        )
    )


def _snapshot(
    database: SqliteDatabase,
    ids: UuidIdGenerator,
    clock: SystemClock,
    event_log: Path,
    now: datetime,
    rotation_backups: int,
) -> dict[str, object]:
    unit = database.read_unit_of_work()
    try:
        dashboard = load_dashboard(
            uow=unit,
            ids=ids,
            clock=clock,
            heartbeat_stale_seconds=heartbeat_stale_seconds(os.environ.get("LING_HEARTBEAT_STALE_SECONDS")),
        )
        try:
            loaded = read_event_log(event_log, backups=rotation_backups)
        except Exception:
            logging.getLogger(__name__).warning("inspector event log read failed")
            loaded_events: tuple[dict[str, object], ...] = ()
            loaded_issues = ({"code": "event_log_unreadable", "reason": "read_failed"},)
        else:
            loaded_events = loaded.events
            loaded_issues = loaded.issues
        return inspect_snapshot(
            now=now,
            slots=_slots(dashboard),
            tickets=_tickets(dashboard),
            events=loaded_events,
            observation_enabled=True,
            log_issues=loaded_issues,
            progress_stale_seconds=progress_stale_seconds(os.environ.get("LING_PROGRESS_STALE_SECONDS")),
            request_timeout_seconds=request_timeout_seconds(os.environ.get("LING_REQUEST_TIMEOUT_SECONDS")),
            inspector_state=None,
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
    rows = []
    for ticket in getattr(dashboard, "tickets", ()):
        rows.append(
            {
                "ticket_id": ticket.ticket_id,
                "state": ticket.state,
                "queue": ticket.queue,
                "target_slot_id": ticket.target_slot_id,
                "claimant": ticket.claimant,
                "review_result": ticket.review_result,
            }
        )
    return tuple(rows)


if __name__ == "__main__":
    raise SystemExit(main())
