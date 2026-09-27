"""Create an attachment session when the presented token hash matches."""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta

from ling.application.attachments import is_token_hash
from ling.application.commands.support import load_slot, parse_slot_id
from ling.application.dto import ATTACHMENT_REJECTED, AttachCommand, AttachResult
from ling.application.ports.attachments import AttachmentSession
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork

_REJECTED = "attachment rejected"
_DUMMY_HASH = "0" * 64


def execute(
    command: AttachCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> AttachResult:
    """Validate the hash and store a new session. Failures share one error."""

    occurred_at = clock.now()
    operation_id = ids.new_operation_id()
    slot_id = parse_slot_id(command.slot_id) if isinstance(command.slot_id, str) else None
    slot = load_slot(uow, slot_id) if slot_id is not None else None
    credential = uow.credentials.get(slot_id.value) if slot_id is not None else None
    stored = credential.token_hash if credential is not None else ""
    presented = command.token_hash if isinstance(command.token_hash, str) else ""
    left = stored if is_token_hash(stored) else _DUMMY_HASH
    right = presented if is_token_hash(presented) else _DUMMY_HASH
    matched = (
        slot is not None
        and credential is not None
        and is_token_hash(stored)
        and is_token_hash(presented)
        and hmac.compare_digest(left, right)
    )
    ttl = command.ttl_seconds
    ttl_ok = isinstance(ttl, int) and not isinstance(ttl, bool) and ttl > 0
    if not matched or not ttl_ok:
        uow.rollback()
        return _rejected(operation_id, occurred_at)
    try:
        expires_at = occurred_at + timedelta(seconds=ttl)
    except OverflowError:
        uow.rollback()
        return _rejected(operation_id, occurred_at)
    session = AttachmentSession(
        session_id=ids.new_session_id(),
        slot_id=slot_id.value if slot_id is not None else "",
        expires_at=expires_at,
        created_at=occurred_at,
    )
    uow.attachment_sessions.save(session)
    previous_id = command.replace_session_id
    if isinstance(previous_id, str) and previous_id.strip() and previous_id != session.session_id:
        previous = uow.attachment_sessions.get(previous_id)
        if previous is not None and previous.revoked_at is None:
            uow.attachment_sessions.save(previous.revoke(occurred_at))
    uow.commit()
    return AttachResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        slot_id=session.slot_id,
        session_id=session.session_id,
        expires_at=expires_at,
        message="attached",
    )


def _rejected(operation_id: str, occurred_at: datetime) -> AttachResult:
    return AttachResult(
        ok=False,
        operation_id=operation_id,
        occurred_at=occurred_at,
        error_code=ATTACHMENT_REJECTED,
        message=_REJECTED,
    )
