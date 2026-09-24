"""Application commands against in-memory ports."""

from __future__ import annotations

from ling.application.commands.acquire_file_lock import execute as acquire_file_lock
from ling.application.commands.claim import execute as claim
from ling.application.commands.consume import execute as consume
from ling.application.commands.dispatch import execute as dispatch
from ling.application.commands.heartbeat import execute as heartbeat
from ling.application.commands.register_slot import execute as register_slot
from ling.application.commands.review import execute as review
from ling.application.commands.submit import execute as submit
from ling.application.dto import (
    AcquireFileLockCommand,
    ClaimCommand,
    ConsumeCommand,
    DispatchCommand,
    HeartbeatCommand,
    RegisterSlotCommand,
    ReviewCommand,
    SubmitCommand,
)
from ling.domain.agents.values import SlotId
from ling.domain.queues import Queue
from ling.domain.tickets.entities import TicketId
from ling.domain.tickets.states import TicketState
from tests.unit.application.fakes import FakeCoordinator, FixedClock, MemoryUnitOfWork, SequenceIds


def _deps(
    failures: set[str] | None = None,
) -> tuple[MemoryUnitOfWork, FakeCoordinator, SequenceIds, FixedClock]:
    return MemoryUnitOfWork(), FakeCoordinator(failures), SequenceIds(), FixedClock()


def _kw(
    uow: MemoryUnitOfWork,
    coordinator: FakeCoordinator,
    ids: SequenceIds,
    clock: FixedClock,
) -> dict[str, object]:
    return {"uow": uow, "coordinator": coordinator, "ids": ids, "clock": clock}


def _register(name: str, template: str, deps: tuple[object, ...]) -> object:
    uow, coordinator, ids, clock = deps
    assert isinstance(uow, MemoryUnitOfWork)
    assert isinstance(coordinator, FakeCoordinator)
    assert isinstance(ids, SequenceIds)
    assert isinstance(clock, FixedClock)
    return register_slot(
        RegisterSlotCommand(slot_id=name, template_id=template),
        **_kw(uow, coordinator, ids, clock),
    )


def test_register_saves_only_after_coordinator_success() -> None:
    deps = _deps()
    uow, coordinator, _, _ = deps
    result = _register("mentor-1", "mentor", deps)
    assert result.ok is True
    assert result.error_code is None
    stored = uow.slots.get(SlotId("mentor-1"))
    assert stored is not None
    assert stored.template.template_id.value == "mentor"
    assert uow.commits == 1
    assert uow.save_log == [("slot", "mentor-1")]
    assert coordinator.calls[0][0] == "register_agent"
    assert result.external_agent_id is None
    assert coordinator.calls[0][1]["slot_id"] == "mentor-1"
    assert "agent_id" not in coordinator.calls[0][1]


def test_register_coordinator_failure_does_not_save_or_commit() -> None:
    deps = _deps({"register_agent"})
    uow, coordinator, _, _ = deps
    commits = uow.commits
    result = _register("mentor-1", "mentor", deps)
    assert result.ok is False
    assert result.error_code == "coordinator_unavailable"
    assert result.external_agent_id is None
    assert uow.slots.get(SlotId("mentor-1")) is None
    assert uow.save_log == []
    assert uow.commits == commits
    assert coordinator.calls[0][0] == "register_agent"


def test_unknown_template_and_duplicate_slot_do_not_call_coordinator() -> None:
    deps = _deps()
    uow, coordinator, _, _ = deps
    missing = _register("mentor-1", "stranger", deps)
    assert missing.ok is False
    assert missing.error_code == "not_found"
    assert coordinator.calls == []
    assert uow.commits == 0
    created = _register("mentor-1", "mentor", deps)
    assert created.ok is True
    before = len(coordinator.calls)
    duplicate = _register("mentor-1", "mentor", deps)
    assert duplicate.error_code == "conflict"
    assert len(coordinator.calls) == before
    assert uow.save_log == [("slot", "mentor-1")]


def test_heartbeat_requires_registered_slot_and_does_not_commit_on_failure() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    missing = heartbeat(HeartbeatCommand("mentor-1"), **_kw(uow, coordinator, ids, clock))
    assert missing.error_code == "not_found"
    assert coordinator.calls == []
    assert uow.commits == 0
    _register("worker-1", "worker", deps)
    failed = _deps({"heartbeat"})
    _register("worker-1", "worker", failed)
    uow_f, coordinator_f, ids_f, clock_f = failed
    commits = uow_f.commits
    refused = heartbeat(HeartbeatCommand("worker-1"), **_kw(uow_f, coordinator_f, ids_f, clock_f))
    assert refused.error_code == "coordinator_unavailable"
    assert uow_f.commits == commits
    alive = heartbeat(HeartbeatCommand("worker-1"), **_kw(uow, coordinator, ids, clock))
    assert alive.ok is True
    assert coordinator.calls[-1][0] == "heartbeat"
    assert coordinator.calls[-1][1]["slot_id"] == "worker-1"
    assert coordinator.calls[-1][1]["agent_id"] is None


def test_only_mentor_can_dispatch_to_worker() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    _register("mentor-1", "mentor", deps)
    _register("worker-1", "worker", deps)
    _register("checker-1", "checker", deps)
    before = len(coordinator.calls)
    worker_dispatch = dispatch(
        DispatchCommand("worker-1", "worker", "do work"),
        **_kw(uow, coordinator, ids, clock),
    )
    checker_dispatch = dispatch(
        DispatchCommand("checker-1", "worker", "do work"),
        **_kw(uow, coordinator, ids, clock),
    )
    mentor_to_checker = dispatch(
        DispatchCommand("mentor-1", "checker", "review me"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert worker_dispatch.error_code == "forbidden"
    assert checker_dispatch.error_code == "forbidden"
    assert mentor_to_checker.error_code == "forbidden"
    assert uow.tickets.get(TicketId("ticket-1")) is None
    assert len(coordinator.calls) == before
    created = dispatch(
        DispatchCommand("mentor-1", "worker", "implement the slice"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert created.ok is True
    assert created.state == "queued"
    assert created.queue == Queue.TASKS.number
    assert uow.save_log[-2:] == [("ticket", "ticket-1"), ("lock", "mentor-1")]
    ticket = uow.tickets.get(TicketId("ticket-1"))
    assert ticket is not None
    assert ticket.claimant is None


def test_open_consumption_lock_blocks_second_dispatch_until_consume() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    _register("mentor-1", "mentor", deps)
    _register("worker-1", "worker", deps)
    _register("checker-1", "checker", deps)
    first = dispatch(
        DispatchCommand("mentor-1", "worker", "first"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert first.ok is True
    blocked = dispatch(
        DispatchCommand("mentor-1", "worker", "second"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert blocked.ok is False
    assert blocked.error_code == "consumption_lock_held"
    assert uow.tickets.get(TicketId("ticket-2")) is None
    assert list(uow._tickets) == ["ticket-1"]
    _drive_to_reviewed(deps, "ticket-1", decision="reject")
    lock = uow.consumption_locks.get(SlotId("mentor-1"))
    assert lock is not None and lock.held is True
    reviewed = uow.tickets.get(TicketId("ticket-1"))
    assert reviewed is not None
    assert reviewed.state is TicketState.REJECTED
    assert reviewed.queue == Queue.RESULTS
    consumed = consume(
        ConsumeCommand("mentor-1", "ticket-1"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert consumed.ok is True
    assert consumed.state == "consumed"
    assert consumed.queue is None
    assert consumed.lock_held is False
    again = dispatch(
        DispatchCommand("mentor-1", "worker", "third"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert again.ok is True
    assert again.ticket_id == "ticket-2"
    assert again.state == "queued"


def test_claim_coordinator_failure_leaves_queue_and_claimant() -> None:
    deps = _deps({"claim_task"})
    uow, coordinator, ids, clock = deps
    _register("mentor-1", "mentor", deps)
    _register("worker-1", "worker", deps)
    dispatch(
        DispatchCommand("mentor-1", "worker", "claim me"),
        **_kw(uow, coordinator, ids, clock),
    )
    commits = uow.commits
    log = list(uow.save_log)
    result = claim(ClaimCommand("worker-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    assert result.ok is False
    assert result.error_code == "coordinator_unavailable"
    assert result.state == "queued"
    assert result.queue == 1
    assert result.claimant is None
    ticket = uow.tickets.get(TicketId("ticket-1"))
    assert ticket is not None
    assert ticket.state is TicketState.QUEUED
    assert ticket.queue == Queue.TASKS
    assert ticket.claimant is None
    assert uow.commits == commits
    assert uow.save_log == log
    lock = uow.consumption_locks.get(SlotId("mentor-1"))
    assert lock is not None and lock.held is True
    assert lock.ticket_id == TicketId("ticket-1")


def test_claim_by_non_worker_does_not_call_coordinator() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    _register("mentor-1", "mentor", deps)
    _register("checker-1", "checker", deps)
    dispatch(
        DispatchCommand("mentor-1", "worker", "stay queued"),
        **_kw(uow, coordinator, ids, clock),
    )
    before = [name for name, _ in coordinator.calls]
    refused = claim(ClaimCommand("checker-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    assert refused.error_code == "forbidden"
    assert [name for name, _ in coordinator.calls] == before
    ticket = uow.tickets.get(TicketId("ticket-1"))
    assert ticket is not None and ticket.state is TicketState.QUEUED


def test_submit_review_and_caller_limits() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    _register("mentor-1", "mentor", deps)
    _register("worker-1", "worker", deps)
    _register("worker-2", "worker", deps)
    _register("checker-1", "checker", deps)
    dispatch(
        DispatchCommand("mentor-1", "worker", "full path"),
        **_kw(uow, coordinator, ids, clock),
    )
    claimed = claim(ClaimCommand("worker-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    assert claimed.ok is True
    assert claimed.claimant == "worker-1"
    assert claimed.state == "claimed"
    before = len(coordinator.calls)
    other = submit(SubmitCommand("worker-2", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    checker_submit = submit(SubmitCommand("checker-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    assert other.error_code == "not_claimant"
    assert checker_submit.error_code == "forbidden"
    still = uow.tickets.get(TicketId("ticket-1"))
    assert still is not None and still.state is TicketState.CLAIMED
    submitted = submit(SubmitCommand("worker-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    assert submitted.ok is True
    assert submitted.state == "submitted"
    assert submitted.queue == Queue.REVIEW.number
    worker_review = review(
        ReviewCommand("worker-1", "ticket-1", "accept"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert worker_review.error_code == "forbidden"
    assert uow.tickets.get(TicketId("ticket-1")).state is TicketState.SUBMITTED  # type: ignore[union-attr]
    accepted = review(
        ReviewCommand("checker-1", "ticket-1", "accept"),
        **_kw(uow, coordinator, ids, clock),
    )
    assert accepted.ok is True
    assert accepted.state == "accepted"
    assert accepted.review_result == "accepted"
    assert accepted.queue == Queue.RESULTS.number
    stranger = consume(ConsumeCommand("worker-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    assert stranger.error_code == "not_issuer"
    lock = uow.consumption_locks.get(SlotId("mentor-1"))
    assert lock is not None and lock.held is True
    done = consume(ConsumeCommand("mentor-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    assert done.ok is True
    assert done.lock_held is False
    assert len(coordinator.calls) == before
    consume_log = [item for item in uow.save_log if item[0] in {"ticket", "lock"}]
    assert ("ticket", "ticket-1") in consume_log
    assert consume_log[-2:] == [("ticket", "ticket-1"), ("lock", "mentor-1")]


def test_file_lock_requires_claimant_and_does_not_change_ticket_on_failure() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    _register("mentor-1", "mentor", deps)
    _register("worker-1", "worker", deps)
    _register("worker-2", "worker", deps)
    dispatch(
        DispatchCommand("mentor-1", "worker", "code ticket"),
        **_kw(uow, coordinator, ids, clock),
    )
    early = acquire_file_lock(
        AcquireFileLockCommand("worker-1", "ticket-1", ("src/a.py",)),
        **_kw(uow, coordinator, ids, clock),
    )
    assert early.error_code == "forbidden"
    assert "acquire_file_lock" not in [name for name, _ in coordinator.calls]
    claim(ClaimCommand("worker-1", "ticket-1"), **_kw(uow, coordinator, ids, clock))
    other = acquire_file_lock(
        AcquireFileLockCommand("worker-2", "ticket-1", ("src/a.py",)),
        **_kw(uow, coordinator, ids, clock),
    )
    assert other.error_code == "forbidden"
    assert "acquire_file_lock" not in [name for name, _ in coordinator.calls]
    failed_deps = _deps({"acquire_file_lock"})
    _copy_registration_and_claim(deps, failed_deps)
    uow_f, coordinator_f, ids_f, clock_f = failed_deps
    commits = uow_f.commits
    refused = acquire_file_lock(
        AcquireFileLockCommand("worker-1", "ticket-1", ("src/a.py",)),
        **_kw(uow_f, coordinator_f, ids_f, clock_f),
    )
    assert refused.error_code == "coordinator_unavailable"
    assert refused.state == "claimed"
    assert refused.claimant == "worker-1"
    assert uow_f.commits == commits
    ticket = uow_f.tickets.get(TicketId("ticket-1"))
    assert ticket is not None and ticket.state is TicketState.CLAIMED
    granted = acquire_file_lock(
        AcquireFileLockCommand("worker-1", "ticket-1", ("src/a.py",)),
        **_kw(uow, coordinator, ids, clock),
    )
    assert granted.ok is True
    assert granted.state == "claimed"
    assert uow.tickets.get(TicketId("ticket-1")).state is TicketState.CLAIMED  # type: ignore[union-attr]


def test_coordinator_agent_id_is_not_derived_from_the_slot_id() -> None:
    """Domain Slot cannot store a coordinator agent id.

    Register returns an id only when the coordinator supplies one, and does
    not write it onto the slot. Later calls forward a caller-supplied id and
    otherwise send None. They do not substitute the Ling slot id.
    """

    deps = _deps()
    uow, coordinator, ids, clock = deps
    coordinator.agent_ids.update(
        {
            "mentor-1": "coord-mentor-9",
            "worker-1": "coord-worker-9",
        }
    )
    mentor = _register("mentor-1", "mentor", deps)
    worker = _register("worker-1", "worker", deps)
    assert mentor.external_agent_id == "coord-mentor-9"
    assert worker.external_agent_id == "coord-worker-9"
    assert mentor.external_agent_id != mentor.slot_id
    assert worker.external_agent_id != worker.slot_id
    stored_worker = uow.slots.get(SlotId("worker-1"))
    assert stored_worker is not None
    assert stored_worker.slot_id.value == "worker-1"
    assert "coord-worker-9" not in repr(stored_worker)
    kwargs = _kw(uow, coordinator, ids, clock)
    missing = heartbeat(HeartbeatCommand("worker-1"), **kwargs)
    supplied = heartbeat(HeartbeatCommand("worker-1", agent_id="coord-worker-9"), **kwargs)
    assert missing.ok is True
    assert supplied.ok is True
    heartbeats = [payload for name, payload in coordinator.calls if name == "heartbeat"]
    assert heartbeats[0]["slot_id"] == "worker-1"
    assert heartbeats[0]["agent_id"] is None
    assert heartbeats[1]["slot_id"] == "worker-1"
    assert heartbeats[1]["agent_id"] == "coord-worker-9"
    before = [name for name, _ in coordinator.calls]
    blank = heartbeat(HeartbeatCommand("worker-1", agent_id=" "), **kwargs)
    assert blank.ok is False
    assert blank.error_code == "invalid_input"
    assert [name for name, _ in coordinator.calls] == before
    created = dispatch(DispatchCommand("mentor-1", "worker", "implement"), **kwargs)
    assert created.ok is True
    blocked = claim(ClaimCommand("worker-1", "ticket-1", agent_id=""), **kwargs)
    assert blocked.error_code == "invalid_input"
    assert uow.tickets.get(TicketId("ticket-1")).state is TicketState.QUEUED  # type: ignore[union-attr]
    claimed = claim(ClaimCommand("worker-1", "ticket-1"), **kwargs)
    assert claimed.ok is True
    assert claimed.claimant == "worker-1"
    claims = [payload for name, payload in coordinator.calls if name == "claim_task"]
    assert len(claims) == 1
    assert claims[0]["slot_id"] == "worker-1"
    assert claims[0]["agent_id"] is None
    granted = acquire_file_lock(
        AcquireFileLockCommand(
            "worker-1",
            "ticket-1",
            ("src/a.py",),
            agent_id="coord-worker-9",
        ),
        **kwargs,
    )
    assert granted.ok is True
    assert granted.state == "claimed"
    locks = [payload for name, payload in coordinator.calls if name == "acquire_file_lock"]
    assert locks[0]["slot_id"] == "worker-1"
    assert locks[0]["agent_id"] == "coord-worker-9"
    assert locks[0]["agent_id"] != locks[0]["slot_id"]
    held = uow.consumption_locks.get(SlotId("mentor-1"))
    assert held is not None and held.held is True
    assert held.ticket_id == TicketId("ticket-1")


def test_coordinator_transport_errors_do_not_change_local_state() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    coordinator.raises.add("register_agent")
    refused = _register("mentor-1", "mentor", deps)
    assert refused.ok is False
    assert refused.error_code == "coordinator_unavailable"
    assert refused.message == "coordinator call failed"
    assert "9889" not in refused.message
    assert "HTTP" not in refused.message
    assert uow.slots.get(SlotId("mentor-1")) is None
    assert uow.commits == 0
    assert uow.save_log == []

    claimed_deps = _deps()
    uow_c, coordinator_c, ids_c, clock_c = claimed_deps
    coordinator_c.raises.add("claim_task")
    _register("mentor-1", "mentor", claimed_deps)
    _register("worker-1", "worker", claimed_deps)
    kwargs = _kw(uow_c, coordinator_c, ids_c, clock_c)
    assert dispatch(DispatchCommand("mentor-1", "worker", "claim me"), **kwargs).ok is True
    commits = uow_c.commits
    log = list(uow_c.save_log)
    result = claim(ClaimCommand("worker-1", "ticket-1"), **kwargs)
    assert result.ok is False
    assert result.error_code == "coordinator_unavailable"
    assert result.message == "coordinator call failed"
    assert "9889" not in result.message
    assert result.state == "queued"
    assert result.queue == 1
    assert result.claimant is None
    ticket = uow_c.tickets.get(TicketId("ticket-1"))
    assert ticket is not None
    assert ticket.state is TicketState.QUEUED
    assert ticket.queue == Queue.TASKS
    assert ticket.claimant is None
    lock = uow_c.consumption_locks.get(SlotId("mentor-1"))
    assert lock is not None and lock.held is True
    assert lock.ticket_id == TicketId("ticket-1")
    assert uow_c.commits == commits
    assert uow_c.save_log == log

    locked_deps = _deps()
    uow_l, coordinator_l, ids_l, clock_l = locked_deps
    _register("mentor-1", "mentor", locked_deps)
    _register("worker-1", "worker", locked_deps)
    locked_kwargs = _kw(uow_l, coordinator_l, ids_l, clock_l)
    assert dispatch(DispatchCommand("mentor-1", "worker", "code ticket"), **locked_kwargs).ok is True
    assert claim(ClaimCommand("worker-1", "ticket-1"), **locked_kwargs).ok is True
    commits = uow_l.commits
    log = list(uow_l.save_log)
    coordinator_l.raises.add("acquire_file_lock")
    denied = acquire_file_lock(
        AcquireFileLockCommand("worker-1", "ticket-1", ("src/a.py",)),
        **locked_kwargs,
    )
    assert denied.ok is False
    assert denied.error_code == "coordinator_unavailable"
    assert denied.message == "coordinator call failed"
    assert "9889" not in denied.message
    assert denied.state == "claimed"
    assert denied.claimant == "worker-1"
    assert uow_l.commits == commits
    assert uow_l.save_log == log
    stayed = uow_l.tickets.get(TicketId("ticket-1"))
    assert stayed is not None and stayed.state is TicketState.CLAIMED
    assert stayed.claimant == SlotId("worker-1")
    stayed_lock = uow_l.consumption_locks.get(SlotId("mentor-1"))
    assert stayed_lock is not None and stayed_lock.held is True


def test_claim_coordinator_already_claimed_does_not_write_claimant() -> None:
    deps = _deps({"claim_task"})
    uow, coordinator, ids, clock = deps
    coordinator.refusal_codes["claim_task"] = "already_claimed"
    _register("mentor-1", "mentor", deps)
    _register("worker-1", "worker", deps)
    kwargs = _kw(uow, coordinator, ids, clock)
    assert dispatch(DispatchCommand("mentor-1", "worker", "stay queued"), **kwargs).ok is True
    commits = uow.commits
    log = list(uow.save_log)
    result = claim(ClaimCommand("worker-1", "ticket-1"), **kwargs)
    assert result.ok is False
    assert result.error_code == "already_claimed"
    assert result.claimant is None
    assert result.state == "queued"
    ticket = uow.tickets.get(TicketId("ticket-1"))
    assert ticket is not None
    assert ticket.state is TicketState.QUEUED
    assert ticket.claimant is None
    lock = uow.consumption_locks.get(SlotId("mentor-1"))
    assert lock is not None and lock.held is True
    assert lock.ticket_id == TicketId("ticket-1")
    assert uow.commits == commits
    assert uow.save_log == log


def test_foreign_coordinator_payload_becomes_a_ling_result() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    _register("worker-1", "worker", deps)
    commits = uow.commits
    log = list(uow.save_log)

    class BadCoordinator:
        def heartbeat(self, **_kwargs: object) -> dict[str, str]:
            return {"status": "HTTP 500"}

    bad = heartbeat(
        HeartbeatCommand("worker-1"),
        uow=uow,
        coordinator=BadCoordinator(),  # type: ignore[arg-type]
        ids=ids,
        clock=clock,
    )
    assert bad.ok is False
    assert bad.error_code == "coordinator_unavailable"
    assert bad.message == "coordinator call failed"
    assert "HTTP" not in bad.message
    assert uow.commits == commits
    assert uow.save_log == log
    stored = uow.slots.get(SlotId("worker-1"))
    assert stored is not None and stored.slot_id.value == "worker-1"
    assert coordinator.calls[-1][0] == "register_agent"


def test_fetched_aggregates_do_not_change_committed_state() -> None:
    deps = _deps()
    uow, coordinator, ids, clock = deps
    _register("mentor-1", "mentor", deps)
    assert dispatch(
        DispatchCommand("mentor-1", "worker", "isolate"),
        **_kw(uow, coordinator, ids, clock),
    ).ok is True
    raw = uow._tickets["ticket-1"]
    fetched = uow.tickets.get(TicketId("ticket-1"))
    assert fetched is not None and fetched is not raw
    fetched.claim(SlotId("worker-1"))
    assert raw.state is TicketState.QUEUED
    assert raw.claimant is None
    assert uow._tickets["ticket-1"] is raw
    again = uow.tickets.get(TicketId("ticket-1"))
    assert again is not None and again.state is TicketState.QUEUED
    raw_lock = uow._locks["mentor-1"]
    fetched_lock = uow.consumption_locks.get(SlotId("mentor-1"))
    assert fetched_lock is not None and fetched_lock is not raw_lock
    fetched_lock.ticket_id = None
    assert raw_lock.held is True
    assert raw_lock.ticket_id == TicketId("ticket-1")


def _drive_to_reviewed(deps: tuple[object, ...], ticket_id: str, *, decision: str) -> None:
    uow, coordinator, ids, clock = deps
    assert isinstance(uow, MemoryUnitOfWork)
    assert isinstance(coordinator, FakeCoordinator)
    assert isinstance(ids, SequenceIds)
    assert isinstance(clock, FixedClock)
    kwargs = _kw(uow, coordinator, ids, clock)
    assert claim(ClaimCommand("worker-1", ticket_id), **kwargs).ok is True
    assert submit(SubmitCommand("worker-1", ticket_id), **kwargs).ok is True
    assert review(ReviewCommand("checker-1", ticket_id, decision), **kwargs).ok is True


def _copy_registration_and_claim(
    source: tuple[object, ...],
    target: tuple[object, ...],
) -> None:
    """Replay a claimed ticket onto a coordinator that fails file-lock calls."""

    src_uow = source[0]
    assert isinstance(src_uow, MemoryUnitOfWork)
    _register("mentor-1", "mentor", target)
    _register("worker-1", "worker", target)
    _register("worker-2", "worker", target)
    uow, coordinator, ids, clock = target
    assert isinstance(uow, MemoryUnitOfWork)
    assert isinstance(coordinator, FakeCoordinator)
    assert isinstance(ids, SequenceIds)
    assert isinstance(clock, FixedClock)
    kwargs = _kw(uow, coordinator, ids, clock)
    assert dispatch(DispatchCommand("mentor-1", "worker", "code ticket"), **kwargs).ok is True
    assert claim(ClaimCommand("worker-1", "ticket-1"), **kwargs).ok is True
    assert src_uow.tickets.get(TicketId("ticket-1")) is not None
