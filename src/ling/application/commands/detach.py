"""Revoke the current attachment session. Repeating this call stays successful."""

from __future__ import annotations

from datetime import datetime

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
    if session is None or session.revoked_at is not None:
        uow.rollback()
        return _ok(operation_id, occurred_at)
    uow.attachment_sessions.save(session.revoke(occurred_at))
    uow.commit()
    return _ok(operation_id, occurred_at)


def _ok(operation_id: str, occurred_at: datetime) -> DetachResult:
    return DetachResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        message="detached",
    )
