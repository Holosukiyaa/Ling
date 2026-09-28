"""Wire SQLite, the local clock, and the MCP stdio server."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import anyio

from ling.bootstrap.runtime import (
    SystemClock,
    UuidIdGenerator,
    attachment_ttl_rejected,
    attachment_ttl_seconds,
    controller_lease_ttl_rejected,
    controller_lease_ttl_seconds,
    heartbeat_stale_seconds,
    new_instance_id,
    positive_seconds,
    positive_seconds_rejected,
    progress_stale_seconds,
    request_timeout_seconds,
    DEFAULT_PING_INTERVAL_SECONDS,
    DEFAULT_PING_TIMEOUT_SECONDS,
    DEFAULT_QUEUE_POLL_SECONDS,
)
from ling.infrastructure.coordinator import observer_from_environ
from ling.infrastructure.observability import configured_backups, event_log_policy, read_state, sink_from_environ
from ling.infrastructure.observability.jsonl_reader import read_event_log
from ling.infrastructure.observability.paths import OutputCollision, collision_reason
from ling.infrastructure.persistence.sqlite import SqliteDatabase
from ling.interfaces.mcp.adapter import ToolDeps, revoke_bound_session
from ling.interfaces.mcp.server import build_server, record_process_event, run_stdio


def compose(database_path: str | Path) -> SqliteDatabase:
    """Open a Ling database file and ensure its schema exists."""

    return SqliteDatabase(database_path)


def serve(database_path: str | Path) -> None:
    """Serve the local MCP tools on stdio until the client disconnects."""

    _refuse_event_log_collision(database_path)
    database = compose(database_path)
    observer = observer_from_environ()
    event_sink = sink_from_environ()
    raw_ttl = os.environ.get("LING_ATTACHMENT_TTL_SECONDS")
    ttl = attachment_ttl_seconds(raw_ttl)
    if attachment_ttl_rejected(raw_ttl):
        logging.getLogger(__name__).warning(
            "LING_ATTACHMENT_TTL_SECONDS is not a positive integer; using %s",
            ttl,
        )
    raw_lease_ttl = os.environ.get("LING_CONTROLLER_LEASE_TTL_SECONDS")
    lease_ttl = controller_lease_ttl_seconds(raw_lease_ttl)
    if controller_lease_ttl_rejected(raw_lease_ttl):
        logging.getLogger(__name__).warning(
            "LING_CONTROLLER_LEASE_TTL_SECONDS is not a positive integer; using %s",
            lease_ttl,
        )
    event_log = str(os.environ.get("LING_EVENT_LOG") or "").strip()
    state_file = str(os.environ.get("LING_RUNTIME_STATE") or "").strip()
    ping_raw = os.environ.get("LING_PROTOCOL_PING_SECONDS")
    ping_timeout_raw = os.environ.get("LING_PROTOCOL_PING_TIMEOUT_SECONDS")
    poll_raw = os.environ.get("LING_QUEUE_POLL_SECONDS")
    _warn_seconds("LING_PROTOCOL_PING_SECONDS", ping_raw)
    _warn_seconds("LING_PROTOCOL_PING_TIMEOUT_SECONDS", ping_timeout_raw)
    _warn_seconds("LING_QUEUE_POLL_SECONDS", poll_raw)
    deps = ToolDeps(
        open_unit_of_work=database.unit_of_work,
        open_read_unit_of_work=database.read_unit_of_work,
        ids=UuidIdGenerator(),
        clock=SystemClock(),
        observer=observer,
        event_sink=event_sink,
        attachment_ttl_seconds=ttl,
        controller_lease_ttl_seconds=lease_ttl,
        heartbeat_stale_seconds=heartbeat_stale_seconds(os.environ.get("LING_HEARTBEAT_STALE_SECONDS")),
        progress_stale_seconds=progress_stale_seconds(os.environ.get("LING_PROGRESS_STALE_SECONDS")),
        request_timeout_seconds=request_timeout_seconds(os.environ.get("LING_REQUEST_TIMEOUT_SECONDS")),
        ping_interval_seconds=positive_seconds(ping_raw, DEFAULT_PING_INTERVAL_SECONDS),
        ping_timeout_seconds=positive_seconds(ping_timeout_raw, DEFAULT_PING_TIMEOUT_SECONDS),
        queue_poll_seconds=positive_seconds(poll_raw, DEFAULT_QUEUE_POLL_SECONDS),
        runtime_instance_id=new_instance_id(),
        observation_enabled=bool(event_log),
        read_events=_event_reader(event_log, configured_backups()),
    )
    if state_file:
        deps.runtime_state["read_inspector_state"] = lambda: read_state(Path(state_file))
    server = build_server(deps)
    record_process_event(deps, "service_started")
    try:
        anyio.run(run_stdio, server, deps)
    finally:
        try:
            record_process_event(deps, "service_stopped")
        finally:
            try:
                revoke_bound_session(deps)
            finally:
                try:
                    _close_optional(event_sink, "runtime event sink close failed")
                finally:
                    try:
                        _close_observer(observer)
                    finally:
                        database.close()


def _close_observer(observer: object) -> None:
    """Stop background projection. This does not touch the Ling database."""

    _close_optional(observer, "coordinator observer close failed")


def _close_optional(resource: object, message: str) -> None:
    """Call close when the object has one. The application port does not require it."""

    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logging.getLogger(__name__).warning(message)


def _warn_seconds(name: str, raw: object) -> None:
    if positive_seconds_rejected(raw):
        logging.getLogger(__name__).warning("%s is not a positive number; using the default", name)


def _refuse_event_log_collision(database_path: str | Path) -> None:
    """Stop before a log, lock, or rotation file can replace the database."""

    raw = str(os.environ.get("LING_EVENT_LOG") or "").strip()
    if not raw:
        return
    max_bytes, backups = event_log_policy()
    reason = collision_reason(
        Path(database_path),
        event_log=Path(raw),
        rotation_backups=backups if max_bytes is not None else 0,
    )
    if reason is not None:
        raise OutputCollision(reason)


def _event_reader(path: str, backups: int):
    """Return events and sanitized read issues. An unset log is not a failure."""

    def read() -> tuple[tuple[dict[str, object], ...], tuple[dict[str, str], ...]]:
        if not path:
            return (), ()
        try:
            loaded = read_event_log(Path(path), backups=backups)
        except Exception:
            return (), ({"code": "event_log_unreadable", "reason": "read_failed"},)
        return loaded.events, loaded.issues

    return read
