"""Commander session checks for the single controller lease."""

from __future__ import annotations

from datetime import datetime, timedelta

from ling.application.commands.support import load_slot, parse_slot_id
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.leases import ControllerLease
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import MENTOR_ID

CONTROLLER_SLOT_ID = "codex-commander"


def expiry_at(now: datetime, ttl: object) -> datetime | None:
    """Return `now + ttl` seconds, or None when `ttl` is not a positive integer."""

    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        return None
    try:
        return now + timedelta(seconds=ttl)
    except OverflowError:
        return None


def operation_label(ids: IdGenerator, supplied: object) -> str:
    """Use a caller operation id when it is non-empty. Otherwise allocate one."""

    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    return ids.new_operation_id()


def commander_is_attached(
    uow: UnitOfWork,
    actor_slot_id: object,
    session_id: object,
    now: datetime,
) -> bool:
    """True when `actor_slot_id` is the live codex-commander session."""

    if not isinstance(actor_slot_id, str) or actor_slot_id.strip() != CONTROLLER_SLOT_ID:
        return False
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    parsed = parse_slot_id(CONTROLLER_SLOT_ID)
    slot = load_slot(uow, parsed) if parsed is not None else None
    if slot is None or slot.template.template_id != MENTOR_ID:
        return False
    if slot.slot_id.value != CONTROLLER_SLOT_ID:
        return False
    session = uow.attachment_sessions.get(session_id.strip())
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return False
    return session.slot_id == CONTROLLER_SLOT_ID


def lease_is_current(lease: ControllerLease, uow: UnitOfWork, now: datetime) -> bool:
    """True when the lease is active, unexpired, and its session is still attached."""

    if not lease.active or lease.released_at is not None:
        return False
    if lease.expires_at <= now or lease.slot_id != CONTROLLER_SLOT_ID:
        return False
    session = uow.attachment_sessions.get(lease.session_id)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return False
    return session.slot_id == lease.slot_id


def release_lease_for_session(uow: UnitOfWork, session_id: str, at: datetime) -> None:
    """Drop the active lease bound to `session_id`. Other holders stay in place."""

    if not session_id:
        return
    active = uow.controller_leases.get_active()
    if active is None or active.session_id != session_id:
        return
    if not active.active or active.released_at is not None:
        return
    uow.controller_leases.save(active.release(at))
