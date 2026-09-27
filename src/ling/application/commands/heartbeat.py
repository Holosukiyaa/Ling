"""Record a slot heartbeat on Ling's own slot record."""

from __future__ import annotations

from ling.application.commands.support import (
    commit_operation,
    load_slot,
    not_found,
    parse_slot_id,
    prepare_operation,
)
from ling.application.dto import CONFLICT, INVALID_INPUT, HeartbeatCommand, HeartbeatResult
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.unit_of_work import UnitOfWork


def execute(
    command: HeartbeatCommand,
    *,
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
) -> HeartbeatResult:
    """Mark a registered slot online and store the heartbeat time."""

    prepared = prepare_operation(
        uow,
        ids,
        clock,
        command.operation_id,
        "heartbeat",
        {"slot_id": command.slot_id},
        HeartbeatResult,
    )
    if prepared.invalid or prepared.conflict or prepared.replay is not None:
        uow.rollback()
        if isinstance(prepared.replay, HeartbeatResult):
            return prepared.replay
        return HeartbeatResult(
            ok=False,
            operation_id=prepared.operation_id,
            occurred_at=prepared.occurred_at,
            error_code=INVALID_INPUT if prepared.invalid else CONFLICT,
            message=(
                "operation id must be a non-empty string"
                if prepared.invalid
                else "operation id was already used for a different request"
            ),
        )
    operation_id = prepared.operation_id
    occurred_at = prepared.occurred_at
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
    try:
        updated = slot.record_heartbeat(occurred_at)
    except ValueError as exc:
        uow.rollback()
        return HeartbeatResult(
            ok=False,
            operation_id=operation_id,
            occurred_at=occurred_at,
            slot_id=slot.slot_id.value,
            error_code=INVALID_INPUT,
            message=str(exc),
        )
    uow.slots.save(updated)
    result = HeartbeatResult(
        ok=True,
        operation_id=operation_id,
        occurred_at=occurred_at,
        slot_id=updated.slot_id.value,
        online=updated.online,
        last_heartbeat_at=updated.last_heartbeat_at,
        message="heartbeat",
    )
    published = commit_operation(uow, prepared, result)
    if isinstance(published, HeartbeatResult):
        return published
    return HeartbeatResult(
        ok=False,
        operation_id=operation_id,
        occurred_at=occurred_at,
        error_code=CONFLICT,
        message="operation id was already used for a different request",
    )
