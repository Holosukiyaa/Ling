"""Shared loading and error mapping for command modules."""

from __future__ import annotations

from collections.abc import Callable

from ling.application.dto import (
    ALREADY_CLAIMED,
    CONFLICT,
    COORDINATOR_UNAVAILABLE,
    INVALID_INPUT,
    INVALID_TRANSITION,
    NOT_FOUND,
)
from ling.application.ports.coordinator import CoordinatorResult
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import Slot, template_catalog
from ling.domain.agents.values import SlotId, TemplateId
from ling.domain.errors import DomainError
from ling.domain.tickets.entities import Ticket, TicketId

_COORDINATOR_CODES = frozenset(
    {COORDINATOR_UNAVAILABLE, CONFLICT, ALREADY_CLAIMED, INVALID_TRANSITION}
)


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


def coordinator_code(result: CoordinatorResult) -> str:
    """Map a failed coordinator result onto a stable application code."""

    if result.code in _COORDINATOR_CODES:
        return result.code
    return COORDINATOR_UNAVAILABLE


def invalid_input(message: str) -> tuple[str, str]:
    """Pair used by commands that reject blank identifiers or content."""

    return INVALID_INPUT, message


def not_found(message: str) -> tuple[str, str]:
    """Pair used when a slot, ticket, template, or lock is missing."""

    return NOT_FOUND, message


def parse_agent_id(value: object) -> str | None:
    """Return a caller-supplied coordinator agent id.

    None means the caller did not provide one. A blank string is invalid.
    The Ling slot id is never substituted.
    """

    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError("coordinator agent id must be a non-empty string when provided")


def coordinator_agent_id(value: object) -> str | None:
    """Pass through an id the coordinator returned. Missing and blank become None."""

    if isinstance(value, str) and value.strip():
        return value
    return None


def call_coordinator(action: Callable[[], CoordinatorResult]) -> CoordinatorResult:
    """Run one port call and turn foreign failures into a coordinator result."""

    try:
        result = action()
    except DomainError as exc:
        return CoordinatorResult(ok=False, code=domain_code(exc), message=exc.message)
    except Exception:
        return CoordinatorResult(
            ok=False,
            code=COORDINATOR_UNAVAILABLE,
            message="coordinator call failed",
        )
    if isinstance(result, CoordinatorResult):
        return result
    return CoordinatorResult(
        ok=False,
        code=COORDINATOR_UNAVAILABLE,
        message="coordinator call failed",
    )
