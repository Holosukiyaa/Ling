"""SQLite persistence boundary for slots, tickets, and consumption locks."""

from __future__ import annotations

from pathlib import Path

import pytest

from ling.application.commands.claim import execute as claim
from ling.application.commands.consume import execute as consume
from ling.application.commands.dispatch import execute as dispatch
from ling.application.commands.heartbeat import execute as heartbeat
from ling.application.commands.register_slot import execute as register_slot
from ling.application.commands.review import execute as review
from ling.application.commands.submit import execute as submit
from ling.application.dto import (
    ClaimCommand,
    ConsumeCommand,
    DispatchCommand,
    HeartbeatCommand,
    RegisterSlotCommand,
    ReviewCommand,
    SubmitCommand,
)
from ling.bootstrap.container import compose
from ling.domain.agents.entities import MENTOR, WORKER, Slot
from ling.domain.agents.values import SlotId
from ling.domain.locks import ConsumptionLock
from ling.domain.queues import Queue
from ling.domain.tickets.entities import Ticket, TicketId
from ling.domain.tickets.states import ReviewResult, TicketState
from ling.infrastructure.persistence.sqlite import DuplicateRecord, SqliteDatabase, StorageError
from ling.infrastructure.persistence.sqlite.connection import connect
from tests.unit.application.fakes import FakeCoordinator, FixedClock, SequenceIds


@pytest.fixture
def database(tmp_path: Path) -> SqliteDatabase:
    store = SqliteDatabase(tmp_path / "ling.sqlite")
    try:
        yield store
    finally:
        store.close()


def test_schema_initializes_and_omits_coordinator_identity(database: SqliteDatabase) -> None:
    SqliteDatabase(database.path)
    connection = connect(database.path)
    try:
        names = {
            row["name"]
            for table in ("slots", "tickets", "consumption_locks")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        slot_columns = [
            row["name"] for row in connection.execute("PRAGMA table_info(slots)")
        ]
        ticket_columns = [
            row["name"] for row in connection.execute("PRAGMA table_info(tickets)")
        ]
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()
    assert slot_columns == ["slot_id", "template_id"]
    assert "queue" not in ticket_columns
    for banned in ("external_agent_id", "agent_id", "online", "heartbeat", "last_heartbeat"):
        assert banned not in names


def test_memory_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="file path"):
        connect(":memory:")


def test_slot_round_trip_across_units(database: SqliteDatabase) -> None:
    original = Slot(SlotId("mentor-1"), MENTOR)
    with database.unit_of_work() as uow:
        uow.slots.save(original)
        uow.commit()
    with database.unit_of_work() as uow:
        loaded = uow.slots.get(SlotId("mentor-1"))
        found = uow.slots.find(SlotId("mentor-1"))
        assert loaded == original
        assert found == original
        assert loaded is not None and loaded.template == MENTOR
        uow.slots.save(loaded)
        uow.commit()
    with database.unit_of_work() as uow:
        assert uow.slots.get(SlotId("mentor-1")) == original


def test_ticket_states_round_trip(database: SqliteDatabase) -> None:
    _save_slots(database)
    tickets = _sample_tickets()
    with database.unit_of_work() as uow:
        for ticket in tickets:
            uow.tickets.save(ticket)
        uow.commit()
    with database.unit_of_work() as uow:
        for ticket in tickets:
            loaded = uow.tickets.get(ticket.ticket_id)
            assert loaded is not None
            assert uow.tickets.find(ticket.ticket_id) is not None
            _assert_same_ticket(loaded, ticket)
            assert loaded.auto_transitions is False


def test_consumption_lock_round_trip_held_and_released(database: SqliteDatabase) -> None:
    _save_slots(database)
    ticket = Ticket(TicketId("ticket-1"), SlotId("mentor-1"), "ship it")
    held = ConsumptionLock(SlotId("mentor-1"))
    held.occupy(ticket.ticket_id)
    with database.unit_of_work() as uow:
        uow.tickets.save(ticket)
        uow.consumption_locks.save(held)
        uow.commit()
    with database.unit_of_work() as uow:
        loaded_lock = uow.consumption_locks.get(SlotId("mentor-1"))
        loaded_ticket = uow.tickets.get(ticket.ticket_id)
        assert loaded_lock is not None and loaded_ticket is not None
        assert loaded_lock.held is True
        assert loaded_lock.ticket_id == ticket.ticket_id
        assert uow.consumption_locks.find(SlotId("mentor-1")) is not None
        loaded_ticket.claim(SlotId("worker-1"))
        loaded_ticket.submit(SlotId("worker-1"))
        loaded_ticket.reject()
        loaded_lock.consume(loaded_ticket, SlotId("mentor-1"))
        uow.tickets.save(loaded_ticket)
        uow.consumption_locks.save(loaded_lock)
        uow.commit()
    with database.unit_of_work() as uow:
        released = uow.consumption_locks.get(SlotId("mentor-1"))
        finished = uow.tickets.get(ticket.ticket_id)
        assert released is not None and finished is not None
        assert released.held is False
        assert released.ticket_id is None
        assert finished.state is TicketState.CONSUMED
        assert finished.queue is None
        assert finished.review_result is ReviewResult.REJECTED


def test_rollback_and_context_exit_leave_no_rows(database: SqliteDatabase) -> None:
    uow = database.unit_of_work()
    try:
        uow.slots.save(Slot(SlotId("mentor-1"), MENTOR))
        uow.rollback()
        uow.commit()
    finally:
        uow.close()
    with database.unit_of_work() as uow:
        assert uow.slots.get(SlotId("mentor-1")) is None

    with database.unit_of_work() as uow:
        uow.slots.save(Slot(SlotId("mentor-1"), MENTOR))
    with database.unit_of_work() as uow:
        assert uow.slots.get(SlotId("mentor-1")) is None

    with pytest.raises(RuntimeError, match="boom"):
        with database.unit_of_work() as uow:
            uow.slots.save(Slot(SlotId("mentor-1"), MENTOR))
            raise RuntimeError("boom")
    with database.unit_of_work() as uow:
        assert uow.slots.get(SlotId("mentor-1")) is None


def test_ticket_and_lock_commit_is_atomic(database: SqliteDatabase) -> None:
    _save_slots(database)
    ticket = Ticket(TicketId("ticket-9"), SlotId("mentor-1"), "together")
    lock = ConsumptionLock(SlotId("mentor-1"))
    lock.occupy(ticket.ticket_id)
    with database.unit_of_work() as uow:
        uow.tickets.save(ticket)
        uow.consumption_locks.save(lock)
        uow.commit()
    with database.unit_of_work() as uow:
        assert uow.tickets.get(ticket.ticket_id) is not None
        assert uow.consumption_locks.get(SlotId("mentor-1")) is not None

    orphan = Ticket(TicketId("ticket-10"), SlotId("mentor-1"), "orphan")
    missing = ConsumptionLock(SlotId("ghost"))
    missing.occupy(orphan.ticket_id)
    with database.unit_of_work() as uow:
        uow.tickets.save(orphan)
        uow.consumption_locks.save(missing)
        with pytest.raises(StorageError, match="missing record") as caught:
            uow.commit()
        assert not isinstance(caught.value, DuplicateRecord)
    with database.unit_of_work() as uow:
        assert uow.tickets.get(orphan.ticket_id) is None
        assert uow.consumption_locks.get(SlotId("ghost")) is None
        assert uow.tickets.get(ticket.ticket_id) is not None


def test_duplicate_keys_do_not_overwrite(database: SqliteDatabase) -> None:
    _save_slots(database)
    with database.unit_of_work() as uow:
        uow.tickets.save(Ticket(TicketId("ticket-1"), SlotId("mentor-1"), "first"))
        uow.consumption_locks.save(_held("mentor-1", "ticket-1"))
        uow.commit()

    with database.unit_of_work() as uow:
        uow.slots.save(Slot(SlotId("mentor-1"), WORKER))
        with pytest.raises(DuplicateRecord, match="slot mentor-1 already exists"):
            uow.commit()
    with database.unit_of_work() as uow:
        loaded = uow.slots.get(SlotId("mentor-1"))
        assert loaded is not None and loaded.template == MENTOR

    with database.unit_of_work() as uow:
        uow.tickets.save(Ticket(TicketId("ticket-1"), SlotId("mentor-1"), "second"))
        with pytest.raises(DuplicateRecord, match="ticket ticket-1 already exists"):
            uow.commit()
    with database.unit_of_work() as uow:
        loaded = uow.tickets.get(TicketId("ticket-1"))
        assert loaded is not None and loaded.content == "first"
        assert loaded.state is TicketState.QUEUED

    with database.unit_of_work() as uow:
        uow.consumption_locks.save(_held("mentor-1", "ticket-1"))
        with pytest.raises(DuplicateRecord, match="consumption lock mentor-1 already exists"):
            uow.commit()
    with database.unit_of_work() as uow:
        loaded = uow.consumption_locks.get(SlotId("mentor-1"))
        assert loaded is not None and loaded.ticket_id == TicketId("ticket-1")


def test_fetched_ticket_mutation_is_not_written_back(database: SqliteDatabase) -> None:
    _save_slots(database)
    with database.unit_of_work() as uow:
        uow.tickets.save(Ticket(TicketId("ticket-1"), SlotId("mentor-1"), "stay"))
        uow.commit()
    with database.unit_of_work() as uow:
        fetched = uow.tickets.get(TicketId("ticket-1"))
        assert fetched is not None
        fetched.claim(SlotId("worker-1"))
    with database.unit_of_work() as uow:
        loaded = uow.tickets.get(TicketId("ticket-1"))
        assert loaded is not None
        assert loaded.state is TicketState.QUEUED
        assert loaded.claimant is None


def test_unknown_template_is_rejected(database: SqliteDatabase) -> None:
    connection = connect(database.path)
    connection.execute(
        "INSERT INTO slots (slot_id, template_id) VALUES ('stray', 'stranger')"
    )
    connection.close()
    with database.unit_of_work() as uow:
        with pytest.raises(StorageError, match="unknown template stranger"):
            uow.slots.get(SlotId("stray"))


def test_bootstrap_compose_opens_the_sqlite_boundary(tmp_path: Path) -> None:
    store = compose(tmp_path / "composed.sqlite")
    try:
        with store.unit_of_work() as uow:
            uow.slots.save(Slot(SlotId("worker-1"), WORKER))
            uow.commit()
        with store.unit_of_work() as uow:
            loaded = uow.slots.get(SlotId("worker-1"))
            assert loaded is not None and loaded.template == WORKER
    finally:
        store.close()


def test_application_commands_round_trip_through_sqlite(database: SqliteDatabase) -> None:
    uow = database.unit_of_work()
    coordinator = FakeCoordinator()
    ids = SequenceIds()
    clock = FixedClock()

    def run(function, command):  # type: ignore[no-untyped-def]
        return function(
            command,
            uow=uow,
            coordinator=coordinator,
            ids=ids,
            clock=clock,
        )

    try:
        assert run(register_slot, RegisterSlotCommand("mentor-1", "mentor")).ok is True
        assert run(register_slot, RegisterSlotCommand("worker-1", "worker")).ok is True
        assert run(register_slot, RegisterSlotCommand("checker-1", "checker")).ok is True
        assert run(heartbeat, HeartbeatCommand("mentor-1")).ok is True
        missing = run(heartbeat, HeartbeatCommand("missing"))
        assert missing.ok is False
        assert missing.error_code == "not_found"
        created = run(dispatch, DispatchCommand("mentor-1", "worker", "implement the slice"))
        assert created.ok is True
        assert created.ticket_id == "ticket-1"
        assert run(claim, ClaimCommand("worker-1", "ticket-1")).ok is True
        assert run(submit, SubmitCommand("worker-1", "ticket-1")).ok is True
        rejected = run(review, ReviewCommand("checker-1", "ticket-1", "reject"))
        assert rejected.ok is True
        assert rejected.state == "rejected"
        assert rejected.queue == Queue.RESULTS.number
        held = uow.consumption_locks.get(SlotId("mentor-1"))
        assert held is not None and held.held is True
        assert run(consume, ConsumeCommand("mentor-1", "ticket-1")).ok is True
    finally:
        uow.close()

    refused = database.unit_of_work()
    try:
        failed = register_slot(
            RegisterSlotCommand("mentor-2", "mentor"),
            uow=refused,
            coordinator=FakeCoordinator({"register_agent"}),
            ids=SequenceIds(),
            clock=FixedClock(),
        )
        assert failed.ok is False
        assert failed.error_code == "coordinator_unavailable"
    finally:
        refused.close()

    with database.unit_of_work() as uow:
        mentor = uow.slots.get(SlotId("mentor-1"))
        ticket = uow.tickets.get(TicketId("ticket-1"))
        lock = uow.consumption_locks.get(SlotId("mentor-1"))
        assert mentor is not None and mentor.template == MENTOR
        assert uow.slots.get(SlotId("mentor-2")) is None
        assert ticket is not None
        assert ticket.state is TicketState.CONSUMED
        assert ticket.queue is None
        assert ticket.claimant == SlotId("worker-1")
        assert ticket.review_result is ReviewResult.REJECTED
        assert ticket.content == "implement the slice"
        assert lock is not None and lock.held is False


def _save_slots(database: SqliteDatabase) -> None:
    with database.unit_of_work() as uow:
        uow.slots.save(Slot(SlotId("mentor-1"), MENTOR))
        uow.slots.save(Slot(SlotId("worker-1"), WORKER))
        uow.commit()


def _sample_tickets() -> list[Ticket]:
    queued = Ticket(TicketId("queued"), SlotId("mentor-1"), "queued body")
    claimed = Ticket(TicketId("claimed"), SlotId("mentor-1"), "claimed body")
    claimed.claim(SlotId("worker-1"))
    submitted = Ticket(TicketId("submitted"), SlotId("mentor-1"), "submitted body")
    submitted.claim(SlotId("worker-1"))
    submitted.submit(SlotId("worker-1"))
    accepted = Ticket(TicketId("accepted"), SlotId("mentor-1"), "accepted body")
    accepted.claim(SlotId("worker-1"))
    accepted.submit(SlotId("worker-1"))
    accepted.accept()
    rejected = Ticket(TicketId("rejected"), SlotId("mentor-1"), "rejected body")
    rejected.claim(SlotId("worker-1"))
    rejected.submit(SlotId("worker-1"))
    rejected.reject()
    consumed = Ticket(TicketId("consumed"), SlotId("mentor-1"), "consumed body")
    consumed.claim(SlotId("worker-1"))
    consumed.submit(SlotId("worker-1"))
    consumed.accept()
    consumed.consume(SlotId("mentor-1"))
    return [queued, claimed, submitted, accepted, rejected, consumed]


def _assert_same_ticket(loaded: Ticket, original: Ticket) -> None:
    assert loaded.ticket_id == original.ticket_id
    assert loaded.issuer == original.issuer
    assert loaded.content == original.content
    assert loaded.state is original.state
    assert loaded.claimant == original.claimant
    assert loaded.review_result is original.review_result
    assert loaded.queue == original.queue


def _held(mentor: str, ticket: str) -> ConsumptionLock:
    lock = ConsumptionLock(SlotId(mentor))
    lock.occupy(TicketId(ticket))
    return lock
