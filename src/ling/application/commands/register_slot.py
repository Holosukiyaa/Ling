"""Register a slot only after the coordinator accepts the agent."""

from __future__ import annotations

from ling.application.commands.support import (
    call_coordinator,
    coordinator_agent_id,
    coordinator_code,
    known_template,
    load_slot,
    not_found,
    parse_slot_id,
    parse_template_id,
)
from ling.application.dto import CONFLICT, INVALID_INPUT, RegisterSlotCommand, RegisterSlotResult
from ling.application.ports.clock import Clock
from ling.application.ports.coordinator import CoordinatorPort
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork
from ling.domain.agents.entities import Slot, template_catalog


def execute(
    command: RegisterSlotCommand,
    *,
    uow: UnitOfWork,
    coordinator: CoordinatorPort,
    ids: IdGenerator,
    clock: Clock,
) -> RegisterSlotResult:
    """Validate the declaration, register externally, then save and commit."""

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
    external = call_coordinator(
        lambda: coordinator.register_agent(
            slot_id=slot_id.value,
            template_id=template_id.value,
            operation_id=operation_id,
        )
    )
    if not external.ok:
        uow.rollback()
        return RegisterSlotResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            slot_id=slot_id.value,
            template_id=template_id.value,
            error_code=coordinator_code(external),
            message=external.message,
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
        external_agent_id=coordinator_agent_id(external.external_id),
        message=external.message,
    )
