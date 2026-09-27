"""Resolve one attachment session to its slot. Expired and revoked sessions fail alike."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ling.application.dto import ATTACHMENT_REJECTED
from ling.application.ports.attachments import AttachmentSessionLookup
from ling.application.ports.clock import Clock
from ling.application.ports.repositories import SlotRepository
from ling.domain.agents.values import SlotId


@dataclass(frozen=True, slots=True)
class ResolvedAttachment:
    """Bound slot, or a stable rejection that does not name the cause."""

    slot_id: str | None
    error_code: str | None
    template_id: str | None = None


class AttachmentResolutionView(Protocol):
    """Session and slot reads. Callers choose a write or deferred read unit."""

    slots: SlotRepository
    attachment_sessions: AttachmentSessionLookup


def execute(session_id: str, *, uow: AttachmentResolutionView, clock: Clock) -> ResolvedAttachment:
    """Return the session slot when it is still active. This query does not write."""

    if not isinstance(session_id, str) or not session_id.strip():
        return ResolvedAttachment(None, ATTACHMENT_REJECTED)
    session = uow.attachment_sessions.get(session_id.strip())
    if session is None or session.revoked_at is not None or session.expires_at <= clock.now():
        return ResolvedAttachment(None, ATTACHMENT_REJECTED)
    try:
        slot_id = SlotId(session.slot_id)
    except ValueError:
        return ResolvedAttachment(None, ATTACHMENT_REJECTED)
    slot = uow.slots.get(slot_id)
    if slot is None:
        return ResolvedAttachment(None, ATTACHMENT_REJECTED)
    return ResolvedAttachment(session.slot_id, None, slot.template.template_id.value)
