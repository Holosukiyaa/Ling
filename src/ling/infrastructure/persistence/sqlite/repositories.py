"""Load and store domain aggregates. SQL stays in this module."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from ling.domain.agents.entities import Slot, template_catalog
from ling.domain.agents.values import SlotId, TemplateId
from ling.domain.locks import ConsumptionLock
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
        staged = self._uow.staged("slot", slot_id.value)
        if isinstance(staged, Slot):
            return staged
        row = self._uow._connection().execute(
            "SELECT slot_id, template_id FROM slots WHERE slot_id = ?",
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


def insert_aggregate(connection: sqlite3.Connection, kind: str, item: object) -> None:
    """Insert one new row. A unique conflict leaves the stored row untouched."""

    try:
        if kind == "slot":
            if not isinstance(item, Slot):
                raise StorageError("slot save received the wrong aggregate")
            connection.execute(
                "INSERT INTO slots (slot_id, template_id) VALUES (?, ?)",
                (item.slot_id.value, item.template.template_id.value),
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
                "UPDATE slots SET template_id = ? WHERE slot_id = ?",
                (item.template.template_id.value, item.slot_id.value),
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
    return Slot(slot_id=SlotId(str(row["slot_id"])), template=template)


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
    return kind


def _label(kind: str) -> str:
    if kind == "lock":
        return "consumption lock"
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
