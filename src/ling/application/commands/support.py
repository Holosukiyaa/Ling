"""Shared loading and error mapping for command modules."""

from __future__ import annotations

from ling.application.dto import INVALID_INPUT, NOT_FOUND
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import Slot, template_catalog
from ling.domain.agents.values import SlotId, TemplateId
from ling.domain.errors import DomainError
from ling.domain.tickets.entities import Ticket, TicketId


def parse_slot_id(value: str) -> SlotId | None:
    """Return a slot id, or None when the raw value is blank."""

    try:
        return SlotId(value)
    except ValueError:
        return None


def parse_template_id(value: str) -> TemplateId | None:
    """Return a template id, or None when the raw value is blank."""

    try:
        return TemplateId(value)
    except ValueError:
        return None


def parse_ticket_id(value: str) -> TicketId | None:
    """Return a ticket id, or None when the raw value is blank."""

    try:
        return TicketId(value)
    except ValueError:
        return None


def parse_paths(paths: tuple[str, ...]) -> frozenset[str] | None:
    """Return stripped paths, or None when any entry is blank."""

    if not paths:
        return None
    cleaned: list[str] = []
    for path in paths:
        if not isinstance(path, str) or not path.strip():
            return None
        cleaned.append(path.strip())
    return frozenset(cleaned)


def load_slot(uow: UnitOfWork, slot_id: SlotId) -> Slot | None:
    """Read a slot from the current unit of work."""

    return uow.slots.get(slot_id)


def load_ticket(uow: UnitOfWork, ticket_id: TicketId) -> Ticket | None:
    """Read a ticket from the current unit of work."""

    return uow.tickets.get(ticket_id)


def known_template(template_id: TemplateId) -> bool:
    """True when `template_id` is one of the initial declarations."""

    return template_id in template_catalog()


def domain_code(exc: DomainError) -> str:
    """Expose the domain error's stable code on the application result."""

    return exc.code


def invalid_input(message: str) -> tuple[str, str]:
    """Pair used by commands that reject blank identifiers or content."""

    return INVALID_INPUT, message


def not_found(message: str) -> tuple[str, str]:
    """Pair used when a slot, ticket, template, or lock is missing."""

    return NOT_FOUND, message
