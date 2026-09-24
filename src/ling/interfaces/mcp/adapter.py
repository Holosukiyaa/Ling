"""Turn MCP arguments into application calls and structured results."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.observer import NullRuntimeObserver, RuntimeObserver
from ling.application.ports.unit_of_work import UnitOfWork
from ling.interfaces.mcp.schemas import parse_input

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolDeps:
    """Local dependencies supplied by bootstrap. No concrete database type."""

    open_unit_of_work: Callable[[], UnitOfWork]
    ids: IdGenerator
    clock: Clock
    observer: RuntimeObserver = field(default_factory=NullRuntimeObserver)


def rejected(deps: ToolDeps, message: str, *, ticket_id: str | None = None) -> dict[str, Any]:
    """Structured refusal for input that never reached an application use case."""

    return {
        "ok": False,
        "operation_id": deps.ids.new_operation_id(),
        "ticket_id": ticket_id,
        "state": None,
        "queue": None,
        "error_code": "invalid_input",
        "message": message,
    }


def call_use_case(deps: ToolDeps, function: Callable[..., Any], command: object) -> dict[str, Any]:
    """Open one unit of work, call the use case, and hide unexpected failures."""

    unit = deps.open_unit_of_work()
    try:
        result = function(command, uow=unit, ids=deps.ids, clock=deps.clock)
        return payload_from(result)
    except Exception:
        logger.exception("use case failed")
        try:
            unit.rollback()
        except Exception:
            logger.exception("rollback failed")
        return {
            "ok": False,
            "operation_id": deps.ids.new_operation_id(),
            "ticket_id": None,
            "state": None,
            "queue": None,
            "error_code": "internal",
            "message": "request failed",
        }
    finally:
        unit.close()


def read_dashboard(deps: ToolDeps, function: Callable[..., Any]) -> dict[str, Any]:
    """Open one unit of work for the read-only dashboard."""

    unit = deps.open_unit_of_work()
    try:
        return payload_from(function(uow=unit, ids=deps.ids, clock=deps.clock))
    except Exception:
        logger.exception("dashboard failed")
        try:
            unit.rollback()
        except Exception:
            logger.exception("rollback failed")
        return {
            "ok": False,
            "operation_id": deps.ids.new_operation_id(),
            "ticket_id": None,
            "state": None,
            "queue": None,
            "error_code": "internal",
            "message": "request failed",
        }
    finally:
        unit.close()


def validated(model: type[BaseModel], arguments: dict[str, Any], deps: ToolDeps) -> tuple[BaseModel | None, dict[str, Any] | None]:
    """Return the parsed input, or a structured invalid-input result."""

    parsed, message = parse_input(model, arguments)
    if message is not None:
        return None, rejected(deps, message)
    return parsed, None


def payload_from(result: object) -> dict[str, Any]:
    """Copy an application result into the shared MCP result shape."""

    payload: dict[str, Any] = {
        "ok": bool(getattr(result, "ok")),
        "operation_id": str(getattr(result, "operation_id")),
        "ticket_id": getattr(result, "ticket_id", None),
        "state": getattr(result, "state", None),
        "queue": getattr(result, "queue", None),
        "error_code": getattr(result, "error_code", None),
        "message": str(getattr(result, "message", "")),
    }
    for name in (
        "slot_id",
        "template_id",
        "claimant",
        "review_result",
        "lock_held",
        "paths",
        "online",
        "last_heartbeat_at",
        "slots",
        "tickets",
        "consumption_locks",
        "file_locks",
    ):
        if hasattr(result, name):
            payload[name] = _jsonable(getattr(result, name))
    return payload


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _jsonable(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    return value
