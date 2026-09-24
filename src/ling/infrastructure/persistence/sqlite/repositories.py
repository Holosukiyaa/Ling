"""Load and store domain aggregates. SQL stays in this module."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

from ling.domain.agents.entities import Slot, template_catalog
from ling.domain.agents.values import SlotId, TemplateId
from ling.domain.locks import ConsumptionLock, FileLock
from ling.domain.tickets.entities import Ticket, TicketId
from ling.domain.tickets.states import ReviewResult, TicketState
from ling.infrastructure.persistence.sqlite.errors import DuplicateRecord, StorageError

if TYPE_CHECKING:
    from ling.infrastructure.persistence.sqlite.unit_of_work import SqliteUnitOfWork


class SqliteSlotRepository:
    """Stage slots on the current unit of work."""

    def __init__(self, unit_of_work: SqliteUnitOfWork) -> None:
        self._uow = unit_of_work

    def get(self, slot_id: SlotId) -> Slot | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("slot", slot_id.value)
        if isinstance(staged, Slot):
            return staged
        row = self._uow._connection().execute(
            "SELECT slot_id, template_id, online, last_heartbeat_at FROM slots WHERE slot_id = ?",
            (slot_id.value,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("slot", slot_id.value)
        return slot_from_row(row)

    def find(self, slot_id: SlotId) -> Slot | None:
        return self.get(slot_id)

    def save(self, slot: Slot) -> None:
        self._uow.stage("slot", slot.slot_id.value, slot)


class SqliteTicketRepository:
    """Stage tickets on the current unit of work."""

    def __init__(self, unit_of_work: SqliteUnitOfWork) -> None:
        self._uow = unit_of_work

    def get(self, ticket_id: TicketId) -> Ticket | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("ticket", ticket_id.value)
        if isinstance(staged, Ticket):
            return staged
        row = self._uow._connection().execute(
            """
            SELECT ticket_id, issuer_slot_id, content, state, claimant_slot_id, review_result
            FROM tickets WHERE ticket_id = ?
            """,
            (ticket_id.value,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("ticket", ticket_id.value)
        return ticket_from_row(row)

    def find(self, ticket_id: TicketId) -> Ticket | None:
        return self.get(ticket_id)

    def save(self, ticket: Ticket) -> None:
        self._uow.stage("ticket", ticket.ticket_id.value, ticket)


class SqliteConsumptionLockRepository:
    """Stage consumption locks on the current unit of work."""

    def __init__(self, unit_of_work: SqliteUnitOfWork) -> None:
        self._uow = unit_of_work

    def get(self, mentor: SlotId) -> ConsumptionLock | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("lock", mentor.value)
        if isinstance(staged, ConsumptionLock):
            return staged
        row = self._uow._connection().execute(
            "SELECT mentor_slot_id, ticket_id FROM consumption_locks WHERE mentor_slot_id = ?",
            (mentor.value,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("lock", mentor.value)
        return lock_from_row(row)

    def find(self, mentor: SlotId) -> ConsumptionLock | None:
        return self.get(mentor)

    def save(self, lock: ConsumptionLock) -> None:
        self._uow.stage("lock", lock.mentor.value, lock)


class SqliteFileLockRepository:
    """Stage one file lock per ticket."""

    def __init__(self, unit_of_work: SqliteUnitOfWork) -> None:
        self._uow = unit_of_work

    def get(self, ticket_id: TicketId) -> FileLock | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("file_lock", ticket_id.value)
        if isinstance(staged, FileLock):
            return staged
        if self._uow.file_lock_released(ticket_id.value):
            return None
        row = self._uow._connection().execute(
            "SELECT ticket_id, holder_slot_id FROM file_locks WHERE ticket_id = ?",
            (ticket_id.value,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("file_lock", ticket_id.value)
        return _file_lock_from_row(self._uow._connection(), row)

    def find(self, ticket_id: TicketId) -> FileLock | None:
        return self.get(ticket_id)

    def held(self) -> tuple[FileLock, ...]:
        self._uow.begin_for_read()
        connection = self._uow._connection()
        found: dict[str, FileLock] = {}
        rows = connection.execute("SELECT ticket_id, holder_slot_id FROM file_locks").fetchall()
        for row in rows:
            ticket_id = str(row["ticket_id"])
            if self._uow.file_lock_released(ticket_id):
                continue
            found[ticket_id] = _file_lock_from_row(connection, row)
        for kind, key, item in self._uow._staged:
            if kind == "file_lock" and isinstance(item, FileLock) and not self._uow.file_lock_released(key):
                found[key] = item
        return tuple(found.values())

    def save(self, lock: FileLock) -> None:
        self._uow._released_file_locks.discard(lock.ticket_id.value)
        self._uow.stage("file_lock", lock.ticket_id.value, lock)

    def release(self, ticket_id: TicketId) -> None:
        self._uow.release_file_lock(ticket_id.value)


def insert_aggregate(connection: sqlite3.Connection, kind: str, item: object) -> None:
    """Insert one new row. A unique conflict leaves the stored row untouched."""

    try:
        if kind == "slot":
            if not isinstance(item, Slot):
                raise StorageError("slot save received the wrong aggregate")
            connection.execute(
                """
                INSERT INTO slots (slot_id, template_id, online, last_heartbeat_at)
                VALUES (?, ?, ?, ?)
                """,
                _slot_values(item),
            )
            return
        if kind == "ticket":
            if not isinstance(item, Ticket):
                raise StorageError("ticket save received the wrong aggregate")
            connection.execute(
                """
                INSERT INTO tickets (
                    ticket_id, issuer_slot_id, content, state, claimant_slot_id, review_result
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                _ticket_values(item),
            )
            return
        if kind == "file_lock":
            if not isinstance(item, FileLock):
                raise StorageError("file lock save received the wrong aggregate")
            _insert_file_lock(connection, item)
            return
        if not isinstance(item, ConsumptionLock):
            raise StorageError("consumption lock save received the wrong aggregate")
        connection.execute(
            "INSERT INTO consumption_locks (mentor_slot_id, ticket_id) VALUES (?, ?)",
            _lock_values(item),
        )
    except sqlite3.IntegrityError as exc:
        raise _integrity(kind, _identity(kind, item), exc) from exc


def update_aggregate(connection: sqlite3.Connection, kind: str, item: object) -> None:
    """Update a row this unit of work already loaded or inserted."""

    try:
        if kind == "slot":
            if not isinstance(item, Slot):
                raise StorageError("slot save received the wrong aggregate")
            cursor = connection.execute(
                """
                UPDATE slots
                SET template_id = ?, online = ?, last_heartbeat_at = ?
                WHERE slot_id = ?
                """,
                (
                    item.template.template_id.value,
                    1 if item.online else 0,
                    _format_time(item.last_heartbeat_at),
                    item.slot_id.value,
                ),
            )
            _require_update(connection, cursor, kind, item.slot_id.value, "SELECT 1 FROM slots WHERE slot_id = ?")
            return
        elif kind == "ticket":
            if not isinstance(item, Ticket):
                raise StorageError("ticket save received the wrong aggregate")
            values = _ticket_values(item)
            cursor = connection.execute(
                """
                UPDATE tickets
                SET issuer_slot_id = ?, content = ?, state = ?,
                    claimant_slot_id = ?, review_result = ?
                WHERE ticket_id = ?
                """,
                (values[1], values[2], values[3], values[4], values[5], values[0]),
            )
            _require_update(
                connection,
                cursor,
                kind,
                item.ticket_id.value,
                "SELECT 1 FROM tickets WHERE ticket_id = ?",
            )
            return
        if kind == "file_lock":
            if not isinstance(item, FileLock):
                raise StorageError("file lock save received the wrong aggregate")
            _replace_file_lock_paths(connection, item)
            return
        else:
            if not isinstance(item, ConsumptionLock):
                raise StorageError("consumption lock save received the wrong aggregate")
            mentor, ticket_id = _lock_values(item)
            cursor = connection.execute(
                "UPDATE consumption_locks SET ticket_id = ? WHERE mentor_slot_id = ?",
                (ticket_id, mentor),
            )
            _require_update(
                connection,
                cursor,
                kind,
                item.mentor.value,
                "SELECT 1 FROM consumption_locks WHERE mentor_slot_id = ?",
            )
    except sqlite3.IntegrityError as exc:
        raise _integrity(kind, _identity(kind, item), exc) from exc


def slot_from_row(row: sqlite3.Row) -> Slot:
    """Rebuild a slot from its template id. Unknown templates are not invented."""

    template_id = TemplateId(str(row["template_id"]))
    try:
        template = template_catalog()[template_id]
    except KeyError as exc:
        raise StorageError(f"slot {row['slot_id']} uses unknown template {template_id.value}") from exc
    return Slot(
        slot_id=SlotId(str(row["slot_id"])),
        template=template,
        online=bool(row["online"]),
        last_heartbeat_at=_parse_time(str(row["slot_id"]), row["last_heartbeat_at"]),
    )


def ticket_from_row(row: sqlite3.Row) -> Ticket:
    """Replay the public ticket transitions until the stored state is reached."""

    ticket_id = str(row["ticket_id"])
    issuer = str(row["issuer_slot_id"])
    content = row["content"]
    if not isinstance(content, str):
        raise StorageError(f"ticket {ticket_id} content is not text")
    state = _state(ticket_id, row["state"])
    claimant = _optional_text(row["claimant_slot_id"])
    review = _review(ticket_id, row["review_result"])
    ticket = Ticket(TicketId(ticket_id), SlotId(issuer), content)
    if state is TicketState.QUEUED:
        return _checked(ticket, state, None, None)
    if claimant is None:
        raise StorageError(f"ticket {ticket_id} in {state.value} has no claimant")
    actor = SlotId(claimant)
    ticket.claim(actor)
    if state is TicketState.CLAIMED:
        return _checked(ticket, state, actor, None)
    ticket.submit(actor)
    if state is TicketState.SUBMITTED:
        return _checked(ticket, state, actor, None)
    if state is TicketState.ACCEPTED:
        ticket.accept()
        return _checked(ticket, state, actor, ReviewResult.ACCEPTED)
    if state is TicketState.REJECTED:
        ticket.reject()
        return _checked(ticket, state, actor, ReviewResult.REJECTED)
    if review is None:
        raise StorageError(f"ticket {ticket_id} was consumed without a review result")
    if review is ReviewResult.ACCEPTED:
        ticket.accept()
    else:
        ticket.reject()
    ticket.consume(SlotId(issuer))
    return _checked(ticket, state, actor, review)


def lock_from_row(row: sqlite3.Row) -> ConsumptionLock:
    """Rebuild a lock. A stored ticket id is occupied through the domain method."""

    lock = ConsumptionLock(SlotId(str(row["mentor_slot_id"])))
    ticket_id = _optional_text(row["ticket_id"])
    if ticket_id is not None:
        lock.occupy(TicketId(ticket_id))
    return lock


def release_file_lock_rows(connection: sqlite3.Connection, ticket_id: str) -> None:
    """Delete one ticket's file lock. Path rows go first because they reference it."""

    connection.execute("DELETE FROM file_lock_paths WHERE ticket_id = ?", (ticket_id,))
    connection.execute("DELETE FROM file_locks WHERE ticket_id = ?", (ticket_id,))


def _slot_values(slot: Slot) -> tuple[str, str, int, str | None]:
    return (
        slot.slot_id.value,
        slot.template.template_id.value,
        1 if slot.online else 0,
        _format_time(slot.last_heartbeat_at),
    )


def _insert_file_lock(connection: sqlite3.Connection, lock: FileLock) -> None:
    connection.execute(
        "INSERT INTO file_locks (ticket_id, holder_slot_id) VALUES (?, ?)",
        (lock.ticket_id.value, lock.holder.value),
    )
    _insert_paths(connection, lock)


def _replace_file_lock_paths(connection: sqlite3.Connection, lock: FileLock) -> None:
    cursor = connection.execute(
        "UPDATE file_locks SET holder_slot_id = ? WHERE ticket_id = ?",
        (lock.holder.value, lock.ticket_id.value),
    )
    _require_update(
        connection,
        cursor,
        "file_lock",
        lock.ticket_id.value,
        "SELECT 1 FROM file_locks WHERE ticket_id = ?",
    )
    connection.execute("DELETE FROM file_lock_paths WHERE ticket_id = ?", (lock.ticket_id.value,))
    _insert_paths(connection, lock)


def _insert_paths(connection: sqlite3.Connection, lock: FileLock) -> None:
    for path in sorted(lock.paths):
        connection.execute(
            "INSERT INTO file_lock_paths (path, ticket_id) VALUES (?, ?)",
            (path, lock.ticket_id.value),
        )


def _file_lock_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> FileLock:
    ticket_id = str(row["ticket_id"])
    path_rows = connection.execute(
        "SELECT path FROM file_lock_paths WHERE ticket_id = ? ORDER BY path",
        (ticket_id,),
    ).fetchall()
    return FileLock(
        TicketId(ticket_id),
        SlotId(str(row["holder_slot_id"])),
        frozenset(str(item["path"]) for item in path_rows),
    )


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_time(slot_id: str, value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise StorageError(f"slot {slot_id} has an unreadable heartbeat time") from exc
    if parsed.tzinfo is None:
        raise StorageError(f"slot {slot_id} heartbeat time has no timezone")
    return parsed


def _ticket_values(ticket: Ticket) -> tuple[str, str, str, str, str | None, str | None]:
    claimant = None if ticket.claimant is None else ticket.claimant.value
    review = None if ticket.review_result is None else ticket.review_result.value
    return (
        ticket.ticket_id.value,
        ticket.issuer.value,
        ticket.content,
        ticket.state.value,
        claimant,
        review,
    )


def _lock_values(lock: ConsumptionLock) -> tuple[str, str | None]:
    ticket_id = None if lock.ticket_id is None else lock.ticket_id.value
    return (lock.mentor.value, ticket_id)


def _require_update(
    connection: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    kind: str,
    key: str,
    exists_sql: str,
) -> None:
    """Accept an update that matched a row even when the values did not change."""

    if cursor.rowcount > 0:
        return
    if connection.execute(exists_sql, (key,)).fetchone() is None:
        raise StorageError(f"{_label(kind)} {key} disappeared before update")


def _identity(kind: str, item: object) -> str:
    if isinstance(item, Slot):
        return item.slot_id.value
    if isinstance(item, Ticket):
        return item.ticket_id.value
    if isinstance(item, ConsumptionLock):
        return item.mentor.value
    if isinstance(item, FileLock):
        return item.ticket_id.value
    return kind


def _label(kind: str) -> str:
    if kind == "lock":
        return "consumption lock"
    if kind == "file_lock":
        return "file lock"
    return kind


def _integrity(kind: str, key: str, exc: sqlite3.IntegrityError) -> StorageError:
    text = str(exc).lower()
    label = _label(kind)
    if "unique" in text or "primary key" in text:
        return DuplicateRecord(f"{label} {key} already exists")
    if "foreign key" in text:
        return StorageError(f"{label} {key} refers to a missing record")
    return StorageError(f"cannot store {label} {key}")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _state(ticket_id: str, value: object) -> TicketState:
    try:
        return TicketState(str(value))
    except ValueError as exc:
        raise StorageError(f"ticket {ticket_id} has unknown state {value!r}") from exc


def _review(ticket_id: str, value: object) -> ReviewResult | None:
    if value is None:
        return None
    try:
        return ReviewResult(str(value))
    except ValueError as exc:
        raise StorageError(f"ticket {ticket_id} has unknown review result {value!r}") from exc


def _checked(
    ticket: Ticket,
    state: TicketState,
    claimant: SlotId | None,
    review: ReviewResult | None,
) -> Ticket:
    if ticket.state is not state or ticket.claimant != claimant or ticket.review_result is not review:
        raise StorageError(
            f"ticket {ticket.ticket_id.value} reloaded as {ticket.state.value}, stored {state.value}"
        )
    return ticket
