"""Release the controller lease held by the attached commander session."""

from __future__ import annotations

from datetime import datetime

from ling.application.commands.support import commit_operation, prepare_operation
from ling.application.controller_lease import (
    CONTROLLER_SLOT_ID,
    commander_is_attached,
    lease_is_current,
    operation_label,
)
from ling.application.dto import (
    CONFLICT,
    FORBIDDEN,
    INVALID_INPUT,
    LEASE_REQUIRED,
    ReleaseControllerLeaseCommand,
    ReleaseControllerLeaseResult,
)
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.leases import ControllerLease
from ling.application.ports.unit_of_work import UnitOfWork

_FORBIDDEN = "controller lease requires the commander"
_REQUIRED = "controller lease required"
_CONFLICT = "operation id was already used for a different request"
_INVALID_OPERATION = "operation id must be a non-empty string"


def execute(
    command: ReleaseControllerLeaseCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> ReleaseControllerLeaseResult:
    """Clear this session's lease. Another holder or an expired lease stays as stored."""

    occurred_at = clock.now()
    if not commander_is_attached(uow, command.actor_slot_id, command.session_id, occurred_at):
        uow.rollback()
        return _denied(ids, command.operation_id, occurred_at, FORBIDDEN, _FORBIDDEN)
    prepared = prepare_operation(
        uow,
        ids,
        clock,
        command.operation_id,
        "release_controller_lease",
        {
            "actor_slot_id": CONTROLLER_SLOT_ID,
            "session_id": command.session_id,
        },
        ReleaseControllerLeaseResult,
    )
    if prepared.invalid or prepared.conflict or prepared.replay is not None:
        uow.rollback()
        if isinstance(prepared.replay, ReleaseControllerLeaseResult):
            return prepared.replay
        return _result(
            prepared.operation_id,
            prepared.occurred_at,
            INVALID_INPUT if prepared.invalid else CONFLICT,
            _INVALID_OPERATION if prepared.invalid else _CONFLICT,
        )
    occurred_at = prepared.occurred_at
    active = uow.controller_leases.get_active()
    if (
        active is None
        or not lease_is_current(active, uow, occurred_at)
        or active.session_id != command.session_id
    ):
        uow.rollback()
        return _result(prepared.operation_id, occurred_at, LEASE_REQUIRED, _REQUIRED)
    released = active.release(occurred_at)
    uow.controller_leases.save(released)
    published = commit_operation(
        uow,
        prepared,
        _ok(prepared.operation_id, occurred_at, released, "released"),
    )
    if isinstance(published, ReleaseControllerLeaseResult):
        return published
    return _result(prepared.operation_id, occurred_at, CONFLICT, _CONFLICT)


def _ok(
    operation_id: str,
    occurred_at: datetime,
    lease: ControllerLease,
    message: str,
) -> ReleaseControllerLeaseResult:
    return ReleaseControllerLeaseResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        lease_id=lease.lease_id,
        slot_id=lease.slot_id,
        session_id=lease.session_id,
        expires_at=lease.expires_at,
        message=message,
    )


def _denied(
    ids: IdGenerator,
    supplied: object,
    occurred_at: datetime,
    code: str,
    message: str,
) -> ReleaseControllerLeaseResult:
    return _result(operation_label(ids, supplied), occurred_at, code, message)


def _result(
    operation_id: str,
    occurred_at: datetime,
    code: str,
    message: str,
) -> ReleaseControllerLeaseResult:
    return ReleaseControllerLeaseResult(
        ok=False,
        operation_id=operation_id,
        occurred_at=occurred_at,
        error_code=code,
        message=message,
    )
