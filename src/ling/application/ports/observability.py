"""Local runtime events. Not domain state and not operation receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

RUNTIME_EVENT_SCHEMA = "ling.runtime_event.v1"
RUNTIME_LIFECYCLE_SCHEMA = "ling.runtime_event.v2"


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Safe summary of one finished MCP request. This shape stays v1."""

    schema: str
    recorded_at: str
    tool: str
    operation_id: str | None
    ok: bool
    error_code: str | None
    ticket_id: str | None
    state: str | None
    queue: int | None
    replay: bool
    duration_ms: int

    def as_dict(self) -> dict[str, object]:
        """Return only the public summary fields."""

        return {
            "schema": self.schema,
            "recorded_at": self.recorded_at,
            "tool": self.tool,
            "operation_id": self.operation_id,
            "ok": self.ok,
            "error_code": self.error_code,
            "ticket_id": self.ticket_id,
            "state": self.state,
            "queue": self.queue,
            "replay": self.replay,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Versioned runtime fact. Missing fields are omitted, not invented."""

    schema: str
    kind: str
    recorded_at: str
    instance_id: str
    request_id: str | None = None
    tool: str | None = None
    slot_id: str | None = None
    operation_id: str | None = None
    ok: bool | None = None
    error_code: str | None = None
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    stage: str | None = None
    commit_state: str | None = None
    duration_ms: int | None = None
    replay: bool | None = None
    reason_code: str | None = None
    retryable: bool | None = None
    next_action: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the non-empty public fields. This is not a business receipt."""

        raw: dict[str, object] = {
            "schema": self.schema,
            "kind": self.kind,
            "recorded_at": self.recorded_at,
            "instance_id": self.instance_id,
            "request_id": self.request_id,
            "tool": self.tool,
            "slot_id": self.slot_id,
            "operation_id": self.operation_id,
            "ok": self.ok,
            "error_code": self.error_code,
            "ticket_id": self.ticket_id,
            "state": self.state,
            "queue": self.queue,
            "stage": self.stage,
            "commit_state": self.commit_state,
            "duration_ms": self.duration_ms,
            "replay": self.replay,
            "reason_code": self.reason_code,
            "retryable": self.retryable,
            "next_action": self.next_action,
        }
        return {key: value for key, value in raw.items() if value is not None}


class RuntimeEventSink(Protocol):
    """Accept one runtime event. Implementations must not affect the caller."""

    def record(self, event: RuntimeEvent | LifecycleEvent) -> None:
        """Store or ignore the event."""


class NullRuntimeEventSink:
    """Event recording that creates no file and no thread."""

    def record(self, event: RuntimeEvent | LifecycleEvent) -> None:
        """Ignore the event."""
