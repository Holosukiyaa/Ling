"""Agent Coordinator port. Results are structured; transport details stay in adapters.

Ling slot ids and coordinator agent ids are different values. `slot_id` is
always the Ling id. `agent_id` and `CoordinatorResult.external_id` are the
coordinator's id when one is known. Nothing here copies one into the other.

Domain `Slot` cannot store the external id, an online flag, or a heartbeat
time. A caller that does not pass `agent_id` causes the port call to send
None. Adapters must not fill `/agents/{id}` with the Ling slot id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CoordinatorResult:
    """Outcome of one coordinator call.

    `code` is an application-level token such as `ok` or `coordinator_unavailable`.
    Adapters translate transport failures into this shape before returning.
    `external_id` is the coordinator agent id when the adapter received one.
    """

    ok: bool
    code: str
    message: str
    external_id: str | None = None


class CoordinatorPort(Protocol):
    """Outbound calls Ling is allowed to make. No lease store lives here."""

    def register_agent(
        self,
        *,
        slot_id: str,
        template_id: str,
        operation_id: str,
    ) -> CoordinatorResult:
        """Register a coordinator agent for a Ling slot. `slot_id` is not an agent id."""

    def heartbeat(
        self,
        *,
        slot_id: str,
        agent_id: str | None,
        operation_id: str,
    ) -> CoordinatorResult:
        """Record one heartbeat. `agent_id` is None when the caller does not know it."""

    def claim_task(
        self,
        *,
        slot_id: str,
        agent_id: str | None,
        ticket_id: str,
        operation_id: str,
    ) -> CoordinatorResult:
        """Ask the coordinator to grant this Ling slot the ticket."""

    def acquire_file_lock(
        self,
        *,
        slot_id: str,
        agent_id: str | None,
        ticket_id: str,
        operation_id: str,
        paths: tuple[str, ...],
    ) -> CoordinatorResult:
        """Ask the coordinator for a file lock. Ling does not store the lease."""
