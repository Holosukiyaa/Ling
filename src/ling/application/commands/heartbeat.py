"""Heartbeat an already registered slot. No background loop lives here."""

from __future__ import annotations

from ling.application.commands.support import (
    call_coordinator,
    coordinator_code,
    load_slot,
    not_found,
    parse_agent_id,
    parse_slot_id,
)
from ling.application.dto import INVALID_INPUT, HeartbeatCommand, HeartbeatResult
from ling.application.ports.clock import Clock
from ling.application.ports.coordinator import CoordinatorPort
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork


def execute(
    command: HeartbeatCommand,
    *,
    uow: UnitOfWork,
    coordinator: CoordinatorPort,
    ids: IdGenerator,
    clock: Clock,
) -> HeartbeatResult:
    """Call the coordinator only when the slot is already stored."""

    operation_id = ids.new_operation_id()
    occurred_at = clock.now()
    slot_id = parse_slot_id(command.slot_id)
    if slot_id is None:
        uow.rollback()
        return HeartbeatResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            error_code=INVALID_INPUT,
            message="slot id must be a non-empty string",
        )
    try:
        agent_id = parse_agent_id(command.agent_id)
    except ValueError as exc:
        uow.rollback()
        return HeartbeatResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            slot_id=slot_id.value,
            error_code=INVALID_INPUT,
            message=str(exc),
        )
    slot = load_slot(uow, slot_id)
    if slot is None:
        uow.rollback()
        code, message = not_found(f"slot {slot_id.value} is not registered")
        return HeartbeatResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            slot_id=slot_id.value,
            error_code=code,
            message=message,
        )
    external = call_coordinator(
        lambda: coordinator.heartbeat(
            slot_id=slot.slot_id.value,
            agent_id=agent_id,
            operation_id=operation_id,
        )
    )
    if not external.ok:
        uow.rollback()
        return HeartbeatResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            slot_id=slot.slot_id.value,
            error_code=coordinator_code(external),
            message=external.message,
        )
    # Domain Slot stores no heartbeat timestamp, so this commit writes no slot.
    uow.commit()
    return HeartbeatResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        slot_id=slot.slot_id.value,
        message=external.message,
    )
