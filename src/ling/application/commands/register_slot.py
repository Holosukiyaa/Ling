"""Register a slot as a local Ling record."""

from __future__ import annotations

from ling.application.commands.support import (
    known_template,
    load_slot,
    not_found,
    parse_slot_id,
    parse_template_id,
)
from ling.application.dto import CONFLICT, INVALID_INPUT, RegisterSlotCommand, RegisterSlotResult
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import Slot, template_catalog


def execute(
    command: RegisterSlotCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> RegisterSlotResult:
    """Validate the template and save the slot. No external registration is required."""

    operation_id = ids.new_operation_id()
    occurred_at = clock.now()
    slot_id = parse_slot_id(command.slot_id)
    template_id = parse_template_id(command.template_id)
    if slot_id is None or template_id is None:
        uow.rollback()
        return RegisterSlotResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            error_code=INVALID_INPUT,
            message="slot id and template id must be non-empty strings",
        )
    if not known_template(template_id):
        uow.rollback()
        code, message = not_found(f"unknown template {template_id.value}")
        return RegisterSlotResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            error_code=code,
            message=message,
        )
    if load_slot(uow, slot_id) is not None:
        uow.rollback()
        return RegisterSlotResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            slot_id=slot_id.value,
            error_code=CONFLICT,
            message=f"slot {slot_id.value} is already registered",
        )
    template = template_catalog()[template_id]
    uow.slots.save(Slot(slot_id=slot_id, template=template))
    uow.commit()
    return RegisterSlotResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        slot_id=slot_id.value,
        template_id=template.template_id.value,
        message="registered",
    )
