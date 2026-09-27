"""Revoke the current attachment session. Repeating this call stays successful."""

from __future__ import annotations

from datetime import datetime

from ling.application.controller_lease import release_lease_for_session
from ling.application.dto import DetachCommand, DetachResult
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork


def execute(
    command: DetachCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> DetachResult:
    """Mark the session revoked. A missing or already revoked session is success."""

    occurred_at = clock.now()
    operation_id = ids.new_operation_id()
    session_id = command.session_id.strip() if isinstance(command.session_id, str) else ""
    if not session_id:
        uow.rollback()
        return _ok(operation_id, occurred_at)
    session = uow.attachment_sessions.get(session_id)
    changed = False
    if session is not None and session.revoked_at is None:
        uow.attachment_sessions.save(session.revoke(occurred_at))
        changed = True
    active = uow.controller_leases.get_active()
    if (
        active is not None
        and active.session_id == session_id
        and active.active
        and active.released_at is None
    ):
        release_lease_for_session(uow, session_id, occurred_at)
        changed = True
    if not changed:
        uow.rollback()
        return _ok(operation_id, occurred_at)
    uow.commit()
    return _ok(operation_id, occurred_at)


def _ok(operation_id: str, occurred_at: datetime) -> DetachResult:
    return DetachResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        message="detached",
    )
