"""Turn MCP arguments into application calls and structured results."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ling.application.attachments import hash_attachment_token
from ling.application.commands.detach import execute as detach_session
from ling.application.dto import (
    ATTACHMENT_REJECTED,
    ATTACHMENT_REQUIRED,
    DetachCommand,
)
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.observability import NullRuntimeEventSink, RuntimeEventSink
from ling.application.ports.observer import NullRuntimeObserver, RuntimeObserver
from ling.application.ports.unit_of_work import UnitOfWork
from ling.application.queries.resolve_attachment import execute as resolve_attachment
from ling.interfaces.mcp.schemas import parse_input

logger = logging.getLogger(__name__)

_OPEN_TOOLS = frozenset({"ling_register_slot", "ling_attach", "ling_detach"})
_BOUND_ACTOR = {
    "ling_heartbeat": "slot_id",
    "ling_dispatch": "issuer_slot_id",
    "ling_claim": "actor_slot_id",
    "ling_abandon_claim": "actor_slot_id",
    "ling_submit": "actor_slot_id",
    "ling_review": "actor_slot_id",
    "ling_consume": "actor_slot_id",
    "ling_acquire_file_lock": "actor_slot_id",
    "ling_acquire_controller_lease": "actor_slot_id",
    "ling_renew_controller_lease": "actor_slot_id",
    "ling_release_controller_lease": "actor_slot_id",
    "ling_provision_slot": "actor_slot_id",
}


@dataclass
class AttachmentBinding:
    """Mutable binding for the one MCP stdio process that owns these dependencies."""

    session_id: str | None = None
    slot_id: str | None = None

    def bind(self, session_id: str, slot_id: str) -> None:
        """Remember the session created for this process."""

        self.session_id = session_id
        self.slot_id = slot_id

    def clear(self) -> None:
        """Forget the process binding. The stored session is revoked separately."""

        self.session_id = None
        self.slot_id = None


@dataclass(frozen=True, slots=True)
class ToolDeps:
    """Local dependencies supplied by bootstrap. No concrete database type."""

    open_unit_of_work: Callable[[], UnitOfWork]
    ids: IdGenerator
    clock: Clock
    observer: RuntimeObserver = field(default_factory=NullRuntimeObserver)
    event_sink: RuntimeEventSink = field(default_factory=NullRuntimeEventSink)
    attachment: AttachmentBinding = field(default_factory=AttachmentBinding)
    attachment_ttl_seconds: int = 3600
    controller_lease_ttl_seconds: int = 3600


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
        "replay",
        "session_id",
        "expires_at",
        "lease_id",
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


def failed(deps: ToolDeps, code: str, message: str) -> dict[str, Any]:
    """Structured refusal that has not called a business use case."""

    return {
        "ok": False,
        "operation_id": deps.ids.new_operation_id(),
        "ticket_id": None,
        "state": None,
        "queue": None,
        "error_code": code,
        "message": message,
    }


def require_text(
    value: object,
    deps: ToolDeps,
    label: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return a non-empty string, or invalid input when the bound value is missing."""

    if not isinstance(value, str) or not value.strip():
        return None, rejected(deps, f"{label} is required")
    return value.strip(), None


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop a raw attachment token. A fingerprint may carry only its hash."""

    if "attachment_token" not in arguments:
        return arguments
    redacted = dict(arguments)
    token = redacted.pop("attachment_token")
    if isinstance(token, str):
        redacted["token_hash"] = hash_attachment_token(token)
    return redacted


def gate_attachment(
    deps: ToolDeps,
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Require a live session and copy its slot into the actor field.

    A supplied actor that differs from the session is forbidden. The business
    use case is not called in that case.
    """

    if name in _OPEN_TOOLS:
        return None, arguments
    slot_id, refusal = _bound_slot(deps)
    if refusal is not None or slot_id is None:
        return refusal or failed(deps, ATTACHMENT_REQUIRED, "attachment required"), arguments
    field = _BOUND_ACTOR.get(name)
    updated = dict(arguments)
    if field is not None:
        if field in updated and updated[field] is not None:
            supplied = updated[field]
            if not isinstance(supplied, str) or supplied.strip() != slot_id:
                return failed(deps, "forbidden", "attached session does not match"), arguments
        updated[field] = slot_id
    return None, updated


def revoke_bound_session(deps: ToolDeps) -> None:
    """Revoke this process's session. This writes nothing to stdout."""

    session_id = deps.attachment.session_id
    try:
        if session_id:
            unit = deps.open_unit_of_work()
            try:
                detach_session(
                    DetachCommand(session_id=session_id),
                    uow=unit,
                    ids=deps.ids,
                    clock=deps.clock,
                )
            finally:
                unit.close()
    except Exception:
        logger.exception("attachment revoke failed")
    finally:
        deps.attachment.clear()


def _bound_slot(deps: ToolDeps) -> tuple[str | None, dict[str, Any] | None]:
    session_id = deps.attachment.session_id
    if not session_id:
        return None, failed(deps, ATTACHMENT_REQUIRED, "attachment required")
    unit = deps.open_unit_of_work()
    try:
        resolved = resolve_attachment(session_id, uow=unit, clock=deps.clock)
    except Exception:
        logger.exception("attachment lookup failed")
        return None, failed(deps, "internal", "request failed")
    finally:
        try:
            unit.close()
        except Exception:
            logger.exception("attachment lookup close failed")
    if resolved.error_code is not None or not resolved.slot_id:
        return None, failed(deps, ATTACHMENT_REJECTED, "attachment rejected")
    if deps.attachment.slot_id != resolved.slot_id:
        return None, failed(deps, ATTACHMENT_REJECTED, "attachment rejected")
    return resolved.slot_id, None
