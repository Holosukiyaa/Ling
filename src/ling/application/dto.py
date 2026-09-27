"""Input and output DTOs shared by future MCP and HTTP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


FORBIDDEN = "forbidden"
INVALID_TRANSITION = "invalid_transition"
ALREADY_CLAIMED = "already_claimed"
NOT_FOUND = "not_found"
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
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterSlotResult:
    """Saved local slot, or a distinguishable failure with no local slot."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    slot_id: str | None = None
    template_id: str | None = None
    error_code: str | None = None
    message: str = ""
    replay: bool = False


@dataclass(frozen=True, slots=True)
class HeartbeatCommand:
    """One heartbeat from an already registered slot."""

    slot_id: str
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    """Local presence update. `online` is true only after a successful heartbeat."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    slot_id: str | None = None
    online: bool | None = None
    last_heartbeat_at: datetime | None = None
    error_code: str | None = None
    message: str = ""
    replay: bool = False


@dataclass(frozen=True, slots=True)
class DispatchCommand:
    """Mentor dispatch toward a template its declaration is allowed to manage.

    `target_slot_id` is optional. Omit it to keep template-only dispatch.
    """

    issuer_slot_id: str
    target_template_id: str
    content: str
    target_slot_id: str | None = None
    operation_id: str | None = None


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
    replay: bool = False


@dataclass(frozen=True, slots=True)
class ClaimCommand:
    """Worker claim of one queued ticket."""

    actor_slot_id: str
    ticket_id: str
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Claim recorded in Ling's own transaction."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    claimant: str | None = None
    error_code: str | None = None
    message: str = ""
    replay: bool = False


@dataclass(frozen=True, slots=True)
class AbandonClaimCommand:
    """Worker releases a claim it currently holds."""

    actor_slot_id: str
    ticket_id: str
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AbandonClaimResult:
    """Ticket returned to queue 1, or the previous state when the call is refused."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    claimant: str | None = None
    error_code: str | None = None
    message: str = ""
    replay: bool = False


@dataclass(frozen=True, slots=True)
class SubmitCommand:
    """Claimant worker submits the ticket for review."""

    actor_slot_id: str
    ticket_id: str
    operation_id: str | None = None


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
    replay: bool = False


@dataclass(frozen=True, slots=True)
class ReviewCommand:
    """Checker decision. `decision` is `accept` or `reject`."""

    actor_slot_id: str
    ticket_id: str
    decision: str
    operation_id: str | None = None


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
    replay: bool = False


@dataclass(frozen=True, slots=True)
class ConsumeCommand:
    """Issuing mentor consumes a reviewed ticket and releases the lock."""

    actor_slot_id: str
    ticket_id: str
    operation_id: str | None = None


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
    replay: bool = False


@dataclass(frozen=True, slots=True)
class AcquireFileLockCommand:
    """Claimant worker records a local file lock for the paths it will write."""

    actor_slot_id: str
    ticket_id: str
    paths: tuple[str, ...]
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AcquireFileLockResult:
    """Local file-lock record. The ticket state is unchanged either way."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    claimant: str | None = None
    paths: tuple[str, ...] | None = None
    error_code: str | None = None
    message: str = ""
    replay: bool = False


@dataclass(frozen=True, slots=True)
class DashboardSlot:
    """One slot as shown by the read-only dashboard."""

    slot_id: str
    template_id: str
    online: bool
    last_heartbeat_at: datetime | None


@dataclass(frozen=True, slots=True)
class DashboardTicket:
    """One ticket as shown by the read-only dashboard."""

    ticket_id: str
    issuer_slot_id: str
    content: str
    state: str
    queue: int | None
    claimant: str | None
    review_result: str | None
    target_slot_id: str | None = None


@dataclass(frozen=True, slots=True)
class DashboardConsumptionLock:
    """One mentor consumption lock as shown by the dashboard."""

    mentor_slot_id: str
    ticket_id: str | None
    held: bool


@dataclass(frozen=True, slots=True)
class DashboardFileLock:
    """One local file lock as shown by the dashboard."""

    ticket_id: str
    holder_slot_id: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DashboardResult:
    """Read-only snapshot of Ling's own records."""

    ok: bool
    operation_id: str
    occurred_at: datetime
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    error_code: str | None = None
    message: str = ""
    slots: tuple[DashboardSlot, ...] = ()
    tickets: tuple[DashboardTicket, ...] = ()
    consumption_locks: tuple[DashboardConsumptionLock, ...] = ()
    file_locks: tuple[DashboardFileLock, ...] = ()
