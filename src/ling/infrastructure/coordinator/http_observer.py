"""Project successful MCP calls to an optional Agent Coordinator.

Ling remains the authority for slots, tickets, and locks. This module only
posts an observation after a local success. It never claims a task, takes a
lock, or starts an agent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from ling.application.ports.observer import NullRuntimeObserver, RuntimeObserver
from ling.infrastructure.coordinator.async_observer import AsyncRuntimeObserver

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 0.5

_SLOT_ARGUMENT = {
    "ling_register_slot": "slot_id",
    "ling_heartbeat": "slot_id",
    "ling_dispatch": "issuer_slot_id",
    "ling_claim": "actor_slot_id",
    "ling_abandon_claim": "actor_slot_id",
    "ling_submit": "actor_slot_id",
    "ling_review": "actor_slot_id",
    "ling_consume": "actor_slot_id",
    "ling_acquire_file_lock": "actor_slot_id",
}
_WORKING = frozenset(
    {
        "ling_dispatch",
        "ling_claim",
        "ling_submit",
        "ling_review",
        "ling_acquire_file_lock",
    }
)
_AWAKE_AFTER_WORK = frozenset({"ling_abandon_claim", "ling_consume"})
_DETAIL_KEYS = (
    "ticket_id",
    "state",
    "queue",
    "operation_id",
    "template_id",
)


def projection_id(slot_id: str) -> str:
    """Stable observer id. It is not stored as a Ling agent id."""

    digest = hashlib.sha256(slot_id.encode("utf-8")).hexdigest()[:24]
    return f"ling-{digest}"


def timeout_seconds(raw: object) -> float:
    """Return a positive timeout. Blank or illegal values use the default."""

    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    text = str(raw).strip()
    if not text:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(text)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if value <= 0 or value != value or value == float("inf"):
        return DEFAULT_TIMEOUT_SECONDS
    return value


def observer_from_environ(environ: Mapping[str, str] | None = None) -> RuntimeObserver:
    """Build the HTTP observer, or a null observer when the URL is unset."""

    env = os.environ if environ is None else environ
    base = str(env.get("LING_COORDINATOR_URL") or "").strip()
    if not base:
        return NullRuntimeObserver()
    workspace = str(env.get("LING_COORDINATOR_WORKSPACE") or "").strip() or os.getcwd()
    api_key = str(env.get("LING_COORDINATOR_API_KEY") or "").strip()
    return AsyncRuntimeObserver(
        HttpRuntimeObserver(
            base_url=base.rstrip("/"),
            workspace=workspace,
            api_key=api_key,
            timeout=timeout_seconds(env.get("LING_COORDINATOR_TIMEOUT")),
        )
    )


class HttpRuntimeObserver:
    """urllib client for register, heartbeat, and activity only."""

    def __init__(self, base_url: str, workspace: str, api_key: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace
        self.api_key = api_key
        self.timeout = timeout

    def observe(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        """Project one successful local call. Failures stay on stderr."""

        try:
            self._observe(tool_name, arguments, result)
        except Exception:
            logger.warning("coordinator observation failed", exc_info=True)

    def _observe(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        if not result.get("ok") or tool_name == "ling_dashboard":
            return
        if not isinstance(arguments, Mapping):
            arguments = {}
        slot_id = _text(arguments.get(_SLOT_ARGUMENT.get(tool_name, "")))
        if slot_id is None:
            slot_id = _text(result.get("slot_id"))
        if slot_id is None or tool_name not in _SLOT_ARGUMENT:
            return
        agent_id = projection_id(slot_id)
        if tool_name == "ling_register_slot":
            self._register(agent_id, arguments, result)
            self._activity(agent_id, tool_name, arguments, result, slot_id)
            return
        if tool_name == "ling_heartbeat":
            self._heartbeat(agent_id, "awake", None)
            return
        if tool_name in _WORKING:
            self._heartbeat(agent_id, "working", _text(result.get("ticket_id")))
            self._activity(agent_id, tool_name, arguments, result, slot_id)
            return
        if tool_name in _AWAKE_AFTER_WORK:
            self._heartbeat(agent_id, "awake", None)
            self._activity(agent_id, tool_name, arguments, result, slot_id)

    def _register(self, agent_id: str, arguments: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        template_id = _text(result.get("template_id")) or _text(arguments.get("template_id")) or ""
        capabilities = ["ling"]
        if template_id:
            capabilities.append(f"template:{template_id}")
        self._post(
            "/agents/register",
            {
                "id": agent_id,
                "workspace": self.workspace,
                "capabilities": capabilities,
                "agent_type": "agent",
                "shadows": [],
                "listens_to": [],
            },
        )

    def _heartbeat(self, agent_id: str, status: str, ticket_id: str | None) -> None:
        self._post(
            f"/agents/{quote(agent_id, safe='')}/heartbeat",
            {"status": status, "current_task_id": ticket_id},
        )

    def _activity(
        self,
        agent_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
        slot_id: str,
    ) -> None:
        action = f"agent_{tool_name}"
        if not action.startswith("agent_"):
            return
        self._post(
            f"/agents/{quote(agent_id, safe='')}/activity",
            {"action": action, "details": _details(arguments, result, slot_id)},
        )

    def _post(self, path: str, body: Mapping[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 0) or response.getcode())
                raw = response.read()
        except urllib.error.HTTPError as exc:
            logger.warning("coordinator %s returned HTTP %s", path, exc.code)
            exc.close()
            return
        except Exception:
            logger.warning("coordinator %s failed", path, exc_info=True)
            return
        if status < 200 or status >= 300:
            logger.warning("coordinator %s returned HTTP %s", path, status)
            return
        if not raw:
            return
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("coordinator %s returned unreadable JSON", path)


def _details(
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    slot_id: str,
) -> dict[str, Any]:
    details: dict[str, Any] = {"slot_id": slot_id}
    for key in _DETAIL_KEYS:
        value = result.get(key)
        if value is not None:
            details[key] = value
    decision = arguments.get("decision")
    if isinstance(decision, str) and decision.strip():
        details["decision"] = decision
    target_slot_id = _text(arguments.get("target_slot_id"))
    if target_slot_id is None:
        target_slot_id = _text(result.get("target_slot_id"))
    if target_slot_id is not None:
        details["target_slot_id"] = target_slot_id
    paths = result.get("paths")
    if not isinstance(paths, list):
        paths = arguments.get("paths")
    if isinstance(paths, list):
        details["paths"] = [str(path) for path in paths]
    return details


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped
