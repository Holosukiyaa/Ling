"""Input and output DTOs shared by future MCP and HTTP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


FORBIDDEN = "forbidden"
INVALID_TRANSITION = "invalid_transition"
ALREADY_CLAIMED = "already_claimed"
NOT_FOUND = "not_found"
COORDINATOR_UNAVAILABLE = "coordinator_unavailable"
CONFLICT = "conflict"
NOT_CLAIMANT = "not_claimant"
NOT_ISSUER = "not_issuer"
CONSUMPTION_LOCK_HELD = "consumption_lock_held"
LOCK_MISMATCH = "lock_mismatch"
INVALID_INPUT = "invalid_input"


@dataclass(frozen=True, slots=True)
class RegisterSlotCommand:
    """Register one slot against a known template declaration."""

    slot_id: str
    template_id: str


@dataclass(frozen=True, slots=True)
class RegisterSlotResult:
    """Saved slot, or a distinguishable failure with no local slot.

    `external_agent_id` is copied only from the coordinator result.
    It is not written onto the slot.
    """

    ok: bool
    operation_id: str
    occurred_at: datetime
    slot_id: str | None = None
    template_id: str | None = None
    external_agent_id: str | None = None
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class HeartbeatCommand:
    """One heartbeat from an already registered slot.

    `agent_id` is the coordinator agent id when the caller has one.
    It is never taken from `slot_id`.
    """

    slot_id: str
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    """Coordinator heartbeat outcome. Domain slot fields are unchanged."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    slot_id: str | None = None
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class DispatchCommand:
    """Mentor dispatch toward a template its declaration is allowed to manage."""

    issuer_slot_id: str
    target_template_id: str
    content: str


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """New queued ticket, or a refusal that did not create one."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ClaimCommand:
    """Worker claim of one queued ticket.

    `agent_id` is forwarded only when the caller supplies it.
    """

    actor_slot_id: str
    ticket_id: str
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Claim recorded only after the coordinator accepts."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    claimant: str | None = None
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class SubmitCommand:
    """Claimant worker submits the ticket for review."""

    actor_slot_id: str
    ticket_id: str


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """Submitted ticket, or the previous state when the caller is refused."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    claimant: str | None = None
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ReviewCommand:
    """Checker decision. `decision` is `accept` or `reject`."""

    actor_slot_id: str
    ticket_id: str
    decision: str


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Accepted or rejected ticket on the result queue. Rejection is success."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    review_result: str | None = None
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ConsumeCommand:
    """Issuing mentor consumes a reviewed ticket and releases the lock."""

    actor_slot_id: str
    ticket_id: str


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    """Consumed ticket. `lock_held` is false only after a successful consume."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    lock_held: bool | None = None
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class AcquireFileLockCommand:
    """Claimant worker asks the coordinator for a file lock.

    `agent_id` is the coordinator id when known, not the Ling slot id.
    """

    actor_slot_id: str
    ticket_id: str
    paths: tuple[str, ...]
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcquireFileLockResult:
    """Coordinator answer. Ling ticket state is unchanged either way."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    claimant: str | None = None
    error_code: str | None = None
    message: str = ""
