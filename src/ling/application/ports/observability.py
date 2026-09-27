"""Local runtime events. Not domain state and not operation receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

RUNTIME_EVENT_SCHEMA = "ling.runtime_event.v1"


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Safe summary of one finished MCP request."""

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


class RuntimeEventSink(Protocol):
    """Accept one runtime event. Implementations must not affect the caller."""

    def record(self, event: RuntimeEvent) -> None:
        """Store or ignore the event."""


class NullRuntimeEventSink:
    """Event recording that creates no file and no thread."""

    def record(self, event: RuntimeEvent) -> None:
        """Ignore the event."""
