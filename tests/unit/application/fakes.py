"""In-memory ports for application tests. Not an infrastructure adapter."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from ling.application.ports.coordinator import CoordinatorResult
from ling.domain.agents.entities import Slot
from ling.domain.agents.values import SlotId
from ling.domain.locks import ConsumptionLock
from ling.domain.tickets.entities import Ticket, TicketId


class MemoryUnitOfWork:
    """Stages saves until `commit`. `rollback` drops the stage only.

    Committed aggregates are copied on read, so mutating a fetched object
    does not change the store until `save` and `commit`.
    """

    def __init__(self) -> None:
        self._slots: dict[str, Slot] = {}
        self._tickets: dict[str, Ticket] = {}
        self._locks: dict[str, ConsumptionLock] = {}
        self._staged: list[tuple[str, object]] = []
        self.save_log: list[tuple[str, str]] = []
        self.commits = 0
        self.rollbacks = 0
        self.slots = _SlotRepository(self)
        self.tickets = _TicketRepository(self)
        self.consumption_locks = _LockRepository(self)

    def commit(self) -> None:
        for kind, item in self._staged:
            if kind == "slot":
                assert isinstance(item, Slot)
                self._slots[item.slot_id.value] = item
                self.save_log.append((kind, item.slot_id.value))
            elif kind == "ticket":
                assert isinstance(item, Ticket)
                self._tickets[item.ticket_id.value] = item
                self.save_log.append((kind, item.ticket_id.value))
            else:
                assert isinstance(item, ConsumptionLock)
                self._locks[item.mentor.value] = item
                self.save_log.append((kind, item.mentor.value))
        self._staged.clear()
        self.commits += 1

    def rollback(self) -> None:
        self._staged.clear()
        self.rollbacks += 1

    def __enter__(self) -> MemoryUnitOfWork:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            self.rollback()


class _SlotRepository:
    def __init__(self, uow: MemoryUnitOfWork) -> None:
        self._uow = uow

    def get(self, slot_id: SlotId) -> Slot | None:
        for kind, item in reversed(self._uow._staged):
            if kind == "slot" and isinstance(item, Slot) and item.slot_id == slot_id:
                return item
        stored = self._uow._slots.get(slot_id.value)
        if stored is None:
            return None
        return copy.deepcopy(stored)

    def find(self, slot_id: SlotId) -> Slot | None:
        return self.get(slot_id)

    def save(self, slot: Slot) -> None:
        self._uow._staged.append(("slot", slot))


class _TicketRepository:
    def __init__(self, uow: MemoryUnitOfWork) -> None:
        self._uow = uow

    def get(self, ticket_id: TicketId) -> Ticket | None:
        for kind, item in reversed(self._uow._staged):
            if kind == "ticket" and isinstance(item, Ticket) and item.ticket_id == ticket_id:
                return item
        stored = self._uow._tickets.get(ticket_id.value)
        if stored is None:
            return None
        return copy.deepcopy(stored)

    def find(self, ticket_id: TicketId) -> Ticket | None:
        return self.get(ticket_id)

    def save(self, ticket: Ticket) -> None:
        self._uow._staged.append(("ticket", ticket))


class _LockRepository:
    def __init__(self, uow: MemoryUnitOfWork) -> None:
        self._uow = uow

    def get(self, mentor: SlotId) -> ConsumptionLock | None:
        for kind, item in reversed(self._uow._staged):
            if kind == "lock" and isinstance(item, ConsumptionLock) and item.mentor == mentor:
                return item
        stored = self._uow._locks.get(mentor.value)
        if stored is None:
            return None
        return copy.deepcopy(stored)

    def find(self, mentor: SlotId) -> ConsumptionLock | None:
        return self.get(mentor)

    def save(self, lock: ConsumptionLock) -> None:
        self._uow._staged.append(("lock", lock))


class FakeCoordinator:
    """Scripted coordinator. Failed methods return a structured refusal.

    Successful `register_agent` returns `external_id` only from `agent_ids`.
    An empty map yields None. The Ling slot id is not copied into that field.
    """

    def __init__(
        self,
        failures: set[str] | None = None,
        *,
        raises: set[str] | None = None,
        agent_ids: dict[str, str] | None = None,
    ) -> None:
        self.failures = set(failures or ())
        self.raises = set(raises or ())
        self.agent_ids = dict(agent_ids or {})
        self.refusal_codes: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def register_agent(
        self,
        *,
        slot_id: str,
        template_id: str,
        operation_id: str,
    ) -> CoordinatorResult:
        return self._answer(
            "register_agent",
            slot_id=slot_id,
            template_id=template_id,
            operation_id=operation_id,
        )

    def heartbeat(
        self,
        *,
        slot_id: str,
        agent_id: str | None,
        operation_id: str,
    ) -> CoordinatorResult:
        return self._answer(
            "heartbeat",
            slot_id=slot_id,
            agent_id=agent_id,
            operation_id=operation_id,
        )

    def claim_task(
        self,
        *,
        slot_id: str,
        agent_id: str | None,
        ticket_id: str,
        operation_id: str,
    ) -> CoordinatorResult:
        return self._answer(
            "claim_task",
            slot_id=slot_id,
            agent_id=agent_id,
            ticket_id=ticket_id,
            operation_id=operation_id,
        )

    def acquire_file_lock(
        self,
        *,
        slot_id: str,
        agent_id: str | None,
        ticket_id: str,
        operation_id: str,
        paths: tuple[str, ...],
    ) -> CoordinatorResult:
        return self._answer(
            "acquire_file_lock",
            slot_id=slot_id,
            agent_id=agent_id,
            ticket_id=ticket_id,
            operation_id=operation_id,
            paths=paths,
        )

    def _answer(self, name: str, **payload: object) -> CoordinatorResult:
        self.calls.append((name, payload))
        if name in self.raises:
            raise ConnectionError(f"HTTP 503 http://127.0.0.1:9889/{name}")
        if name in self.failures:
            return CoordinatorResult(
                ok=False,
                code=self.refusal_codes.get(name, "coordinator_unavailable"),
                message=f"{name} failed",
            )
        external_id = None
        if name == "register_agent":
            supplied = self.agent_ids.get(str(payload.get("slot_id")))
            if isinstance(supplied, str) and supplied.strip():
                external_id = supplied
        return CoordinatorResult(ok=True, code="ok", message=name, external_id=external_id)


class SequenceIds:
    """Deterministic ticket and operation ids."""

    def __init__(self) -> None:
        self._operations = 0
        self._tickets = 0

    def new_operation_id(self) -> str:
        self._operations += 1
        return f"op-{self._operations}"

    def new_ticket_id(self) -> str:
        self._tickets += 1
        return f"ticket-{self._tickets}"


class FixedClock:
    """One frozen instant."""

    def __init__(self) -> None:
        self.instant = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.instant
