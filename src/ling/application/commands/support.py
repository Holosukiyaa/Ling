"""Shared loading and error mapping for command modules."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

from ling.application.dto import INVALID_INPUT, NOT_FOUND
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.operation_receipts import (
    OperationReceipt,
    OperationReceiptTaken,
)
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


@dataclass(frozen=True, slots=True)
class PreparedOperation:
    """Caller-supplied or generated identity for one command attempt."""

    operation_id: str
    occurred_at: datetime
    command_name: str
    fingerprint: str
    replay: object | None = None
    invalid: bool = False
    conflict: bool = False


def request_fingerprint(command_name: str, fields: Mapping[str, object]) -> str:
    """Hash the tool name and normalized business fields. The operation id is not included."""

    normalized: dict[str, object] = {}
    for key in sorted(fields):
        value = fields[key]
        if key == "paths":
            value = _path_fingerprint(value)
        normalized[key] = value
    body = json.dumps(
        {"command": command_name, "fields": normalized},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def prepare_operation(
    uow: UnitOfWork,
    ids: IdGenerator,
    clock: Clock,
    supplied: str | None,
    command_name: str,
    fields: Mapping[str, object],
    result_type: type,
) -> PreparedOperation:
    """Resolve an operation id. A stored success with the same fingerprint is replayed."""

    occurred_at = clock.now()
    fingerprint = request_fingerprint(command_name, fields)
    if supplied is None:
        return PreparedOperation(ids.new_operation_id(), occurred_at, command_name, fingerprint)
    if not isinstance(supplied, str) or not supplied.strip():
        return PreparedOperation(
            ids.new_operation_id(),
            occurred_at,
            command_name,
            fingerprint,
            invalid=True,
        )
    operation_id = supplied.strip()
    existing = uow.operation_receipts.get(operation_id)
    if existing is None:
        return PreparedOperation(operation_id, occurred_at, command_name, fingerprint)
    if existing.command_name == command_name and existing.fingerprint == fingerprint:
        return PreparedOperation(
            operation_id,
            occurred_at,
            command_name,
            fingerprint,
            replay=decode_result(result_type, existing.result_json),
        )
    return PreparedOperation(operation_id, occurred_at, command_name, fingerprint, conflict=True)


def commit_operation(uow: UnitOfWork, prepared: PreparedOperation, result: object) -> object | None:
    """Store the success receipt and commit. A lost race replays the stored success."""

    uow.operation_receipts.save(
        OperationReceipt(
            operation_id=prepared.operation_id,
            command_name=prepared.command_name,
            fingerprint=prepared.fingerprint,
            result_json=encode_result(result),
            created_at=prepared.occurred_at,
        )
    )
    try:
        uow.commit()
    except OperationReceiptTaken:
        uow.rollback()
        existing = uow.operation_receipts.get(prepared.operation_id)
        if (
            existing is not None
            and existing.command_name == prepared.command_name
            and existing.fingerprint == prepared.fingerprint
        ):
            return decode_result(type(result), existing.result_json)
        return None
    return result


def encode_result(result: object) -> str:
    """Serialize a success result without its internal replay flag."""

    packed = _pack(asdict(result))
    if isinstance(packed, dict):
        packed.pop("replay", None)
    return json.dumps(packed, separators=(",", ":"), ensure_ascii=False)


def decode_result(result_type: type, payload: str) -> object:
    """Rebuild a success result and mark it as a replay."""

    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise ValueError("stored operation result is not an object")
    raw.pop("replay", None)
    hints = {item.name: str(item.type) for item in fields(result_type)}
    values = {key: _unpack(value, hints.get(key, "")) for key, value in raw.items()}
    values["replay"] = True
    return result_type(**values)


def _path_fingerprint(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return []
        cleaned.append(item.strip())
    return sorted(set(cleaned))


def _pack(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, dict):
        return {str(key): _pack(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_pack(item) for item in value]
    return value


def _unpack(value: Any, hint: str) -> Any:
    if isinstance(value, dict) and set(value) == {"$datetime"}:
        return datetime.fromisoformat(str(value["$datetime"]))
    if isinstance(value, list) and "tuple" in hint:
        return tuple(_unpack(item, "") for item in value)
    return value
