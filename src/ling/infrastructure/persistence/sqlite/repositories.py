"""Load and store domain aggregates. SQL stays in this module."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Protocol

from ling.application.ports.attachments import AttachmentSession, SlotCredential
from ling.application.ports.leases import ControllerLease, ControllerLeaseHeld
from ling.application.ports.operation_receipts import OperationReceipt, OperationReceiptTaken
from ling.domain.agents.entities import Slot, template_catalog
from ling.domain.agents.values import SlotId, TemplateId
from ling.domain.locks import ConsumptionLock, FileLock
from ling.domain.tickets.entities import Ticket, TicketId
from ling.domain.tickets.states import ReviewResult, TicketState
from ling.infrastructure.persistence.sqlite.errors import DuplicateRecord, StorageError


class SqlUnit(Protocol):
    """Stage and connection surface shared by write and read units of work."""

    _staged: list[tuple[str, str, object]]
    _released_file_locks: set[str]

    def begin_for_read(self) -> None:
        """Start this unit's transaction before a domain read."""

    def staged(self, kind: str, key: str) -> object | None:
        """Return the newest staged aggregate of this identity, if any."""

    def note_loaded(self, kind: str, key: str) -> None:
        """Remember that `key` already has a row."""

    def stage(self, kind: str, key: str, item: object) -> None:
        """Remember `item` until commit."""

    def _connection(self) -> sqlite3.Connection:
        """The open connection."""

    def file_lock_released(self, ticket_id: str) -> bool:
        """True when this unit has staged that ticket's file lock for removal."""

    def release_file_lock(self, ticket_id: str) -> None:
        """Stage removal of one file lock."""


class SqliteSlotRepository:
    """Stage slots on the current unit of work."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
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

    def list(self) -> tuple[Slot, ...]:
        self._uow.begin_for_read()
        rows = self._uow._connection().execute(
            """
            SELECT slot_id, template_id, online, last_heartbeat_at
            FROM slots ORDER BY slot_id
            """
        ).fetchall()
        found = {str(row["slot_id"]): slot_from_row(row) for row in rows}
        for key, item in _staged(self._uow, "slot"):
            if isinstance(item, Slot):
                found[key] = item
        return tuple(found[key] for key in sorted(found))

    def save(self, slot: Slot) -> None:
        self._uow.stage("slot", slot.slot_id.value, slot)


class SqliteTicketRepository:
    """Stage tickets on the current unit of work."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
        self._uow = unit_of_work

    def get(self, ticket_id: TicketId) -> Ticket | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("ticket", ticket_id.value)
        if isinstance(staged, Ticket):
            return staged
        row = self._uow._connection().execute(
            """
            SELECT ticket_id, issuer_slot_id, content, state, claimant_slot_id, review_result,
                   target_slot_id
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

    def list(self) -> tuple[Ticket, ...]:
        self._uow.begin_for_read()
        rows = self._uow._connection().execute(
            """
            SELECT ticket_id, issuer_slot_id, content, state, claimant_slot_id, review_result,
                   target_slot_id
            FROM tickets ORDER BY ticket_id
            """
        ).fetchall()
        found = {str(row["ticket_id"]): ticket_from_row(row) for row in rows}
        for key, item in _staged(self._uow, "ticket"):
            if isinstance(item, Ticket):
                found[key] = item
        return tuple(found[key] for key in sorted(found))

    def save(self, ticket: Ticket) -> None:
        self._uow.stage("ticket", ticket.ticket_id.value, ticket)


class SqliteConsumptionLockRepository:
    """Stage consumption locks on the current unit of work."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
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

    def list(self) -> tuple[ConsumptionLock, ...]:
        self._uow.begin_for_read()
        rows = self._uow._connection().execute(
            "SELECT mentor_slot_id, ticket_id FROM consumption_locks ORDER BY mentor_slot_id"
        ).fetchall()
        found = {str(row["mentor_slot_id"]): lock_from_row(row) for row in rows}
        for key, item in _staged(self._uow, "lock"):
            if isinstance(item, ConsumptionLock):
                found[key] = item
        return tuple(found[key] for key in sorted(found))

    def save(self, lock: ConsumptionLock) -> None:
        self._uow.stage("lock", lock.mentor.value, lock)


class SqliteFileLockRepository:
    """Stage one file lock per ticket."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
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


class SqliteOperationReceiptRepository:
    """Stage one receipt per operation id."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
        self._uow = unit_of_work

    def get(self, operation_id: str) -> OperationReceipt | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("operation_receipt", operation_id)
        if isinstance(staged, OperationReceipt):
            return staged
        row = self._uow._connection().execute(
            """
            SELECT operation_id, command_name, fingerprint, result_json, created_at
            FROM operation_receipts WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("operation_receipt", operation_id)
        return _receipt_from_row(row)

    def save(self, receipt: OperationReceipt) -> None:
        self._uow.stage("operation_receipt", receipt.operation_id, receipt)


class SqliteSlotCredentialRepository:
    """Stage one SHA-256 credential per slot. The raw token is never written."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
        self._uow = unit_of_work

    def get(self, slot_id: str) -> SlotCredential | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("slot_credential", slot_id)
        if isinstance(staged, SlotCredential):
            return staged
        row = self._uow._connection().execute(
            "SELECT slot_id, token_hash, created_at FROM slot_credentials WHERE slot_id = ?",
            (slot_id,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("slot_credential", slot_id)
        return _credential_from_row(row)

    def save(self, credential: SlotCredential) -> None:
        self._uow.stage("slot_credential", credential.slot_id, credential)


class SqliteAttachmentSessionRepository:
    """Stage attachment sessions. Revocation is an update of the same row."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
        self._uow = unit_of_work

    def get(self, session_id: str) -> AttachmentSession | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("attachment_session", session_id)
        if isinstance(staged, AttachmentSession):
            return staged
        row = self._uow._connection().execute(
            """
            SELECT session_id, slot_id, expires_at, created_at, revoked_at
            FROM attachment_sessions WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("attachment_session", session_id)
        return _session_from_row(row)

    def save(self, session: AttachmentSession) -> None:
        self._uow.stage("attachment_session", session.session_id, session)


class SqliteControllerLeaseRepository:
    """Stage the single active controller lease and its released history."""

    def __init__(self, unit_of_work: SqlUnit) -> None:
        self._uow = unit_of_work

    def get(self, lease_id: str) -> ControllerLease | None:
        self._uow.begin_for_read()
        staged = self._uow.staged("controller_lease", lease_id)
        if isinstance(staged, ControllerLease):
            return staged
        row = self._uow._connection().execute(
            """
            SELECT lease_id, slot_id, session_id, acquired_at, expires_at, released_at, active_key
            FROM controller_leases WHERE lease_id = ?
            """,
            (lease_id,),
        ).fetchone()
        if row is None:
            return None
        self._uow.note_loaded("controller_lease", lease_id)
        return _lease_from_row(row)

    def get_active(self) -> ControllerLease | None:
        self._uow.begin_for_read()
        seen: set[str] = set()
        for key, item in reversed(_staged(self._uow, "controller_lease")):
            if not isinstance(item, ControllerLease) or key in seen:
                continue
            seen.add(key)
            if item.active and item.released_at is None:
                return item
        row = self._uow._connection().execute(
            """
            SELECT lease_id, slot_id, session_id, acquired_at, expires_at, released_at, active_key
            FROM controller_leases WHERE active_key = 1
            """
        ).fetchone()
        if row is None:
            return None
        lease_id = str(row["lease_id"])
        if lease_id in seen:
            return None
        self._uow.note_loaded("controller_lease", lease_id)
        return _lease_from_row(row)

    def save(self, lease: ControllerLease) -> None:
        self._uow.stage("controller_lease", lease.lease_id, lease)


def _receipt_from_row(row: sqlite3.Row) -> OperationReceipt:
    created_at = row["created_at"]
    if not isinstance(created_at, str):
        raise StorageError(f"operation {row['operation_id']} has no created time")
    try:
        parsed = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise StorageError(f"operation {row['operation_id']} has an unreadable created time") from exc
    return OperationReceipt(
        operation_id=str(row["operation_id"]),
        command_name=str(row["command_name"]),
        fingerprint=str(row["fingerprint"]),
        result_json=str(row["result_json"]),
        created_at=parsed,
    )


def _staged(unit_of_work: SqlUnit, kind: str) -> list[tuple[str, object]]:
    return [(key, item) for staged_kind, key, item in unit_of_work._staged if staged_kind == kind]


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
                    ticket_id, issuer_slot_id, content, state, claimant_slot_id, review_result,
                    target_slot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                _ticket_values(item),
            )
            return
        if kind == "file_lock":
            if not isinstance(item, FileLock):
                raise StorageError("file lock save received the wrong aggregate")
            _insert_file_lock(connection, item)
            return
        if kind == "operation_receipt":
            if not isinstance(item, OperationReceipt):
                raise StorageError("operation receipt save received the wrong aggregate")
            _insert_operation_receipt(connection, item)
            return
        if kind == "slot_credential":
            if not isinstance(item, SlotCredential):
                raise StorageError("slot credential save received the wrong record")
            _insert_credential(connection, item)
            return
        if kind == "attachment_session":
            if not isinstance(item, AttachmentSession):
                raise StorageError("attachment session save received the wrong record")
            _insert_session(connection, item)
            return
        if kind == "controller_lease":
            if not isinstance(item, ControllerLease):
                raise StorageError("controller lease save received the wrong record")
            _insert_lease(connection, item)
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
                    claimant_slot_id = ?, review_result = ?, target_slot_id = ?
                WHERE ticket_id = ?
                """,
                (values[1], values[2], values[3], values[4], values[5], values[6], values[0]),
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
        if kind == "slot_credential":
            if not isinstance(item, SlotCredential):
                raise StorageError("slot credential save received the wrong record")
            _update_credential(connection, item)
            return
        if kind == "attachment_session":
            if not isinstance(item, AttachmentSession):
                raise StorageError("attachment session save received the wrong record")
            _update_session(connection, item)
            return
        if kind == "controller_lease":
            if not isinstance(item, ControllerLease):
                raise StorageError("controller lease save received the wrong record")
            _update_lease(connection, item)
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
    ticket = Ticket(
        TicketId(ticket_id),
        SlotId(issuer),
        content,
        target_slot_id=_target_slot(ticket_id, row["target_slot_id"]),
    )
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


def _credential_from_row(row: sqlite3.Row) -> SlotCredential:
    slot_id = str(row["slot_id"])
    return SlotCredential(
        slot_id=slot_id,
        token_hash=str(row["token_hash"]),
        created_at=_aware_time(f"slot credential {slot_id}", row["created_at"]),
    )


def _session_from_row(row: sqlite3.Row) -> AttachmentSession:
    session_id = str(row["session_id"])
    revoked = row["revoked_at"]
    return AttachmentSession(
        session_id=session_id,
        slot_id=str(row["slot_id"]),
        expires_at=_aware_time(f"attachment session {session_id}", row["expires_at"]),
        created_at=_aware_time(f"attachment session {session_id}", row["created_at"]),
        revoked_at=None
        if revoked is None
        else _aware_time(f"attachment session {session_id}", revoked),
    )


def _insert_credential(connection: sqlite3.Connection, credential: SlotCredential) -> None:
    connection.execute(
        """
        INSERT INTO slot_credentials (slot_id, token_hash, created_at)
        VALUES (?, ?, ?)
        """,
        (credential.slot_id, credential.token_hash, credential.created_at.isoformat()),
    )


def _update_credential(connection: sqlite3.Connection, credential: SlotCredential) -> None:
    cursor = connection.execute(
        """
        UPDATE slot_credentials
        SET token_hash = ?, created_at = ?
        WHERE slot_id = ?
        """,
        (credential.token_hash, credential.created_at.isoformat(), credential.slot_id),
    )
    _require_update(
        connection,
        cursor,
        "slot_credential",
        credential.slot_id,
        "SELECT 1 FROM slot_credentials WHERE slot_id = ?",
    )


def _insert_session(connection: sqlite3.Connection, session: AttachmentSession) -> None:
    connection.execute(
        """
        INSERT INTO attachment_sessions (
            session_id, slot_id, expires_at, created_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        _session_values(session),
    )


def _update_session(connection: sqlite3.Connection, session: AttachmentSession) -> None:
    values = _session_values(session)
    cursor = connection.execute(
        """
        UPDATE attachment_sessions
        SET slot_id = ?, expires_at = ?, created_at = ?, revoked_at = ?
        WHERE session_id = ?
        """,
        (values[1], values[2], values[3], values[4], values[0]),
    )
    _require_update(
        connection,
        cursor,
        "attachment_session",
        session.session_id,
        "SELECT 1 FROM attachment_sessions WHERE session_id = ?",
    )


def _lease_from_row(row: sqlite3.Row) -> ControllerLease:
    lease_id = str(row["lease_id"])
    released = row["released_at"]
    return ControllerLease(
        lease_id=lease_id,
        slot_id=str(row["slot_id"]),
        session_id=str(row["session_id"]),
        acquired_at=_aware_time(f"controller lease {lease_id}", row["acquired_at"]),
        expires_at=_aware_time(f"controller lease {lease_id}", row["expires_at"]),
        released_at=None
        if released is None
        else _aware_time(f"controller lease {lease_id}", released),
        active=row["active_key"] == 1,
    )


def _lease_values(
    lease: ControllerLease,
) -> tuple[str, str, str, str, str, str | None, int | None]:
    return (
        lease.lease_id,
        lease.slot_id,
        lease.session_id,
        lease.acquired_at.isoformat(),
        lease.expires_at.isoformat(),
        None if lease.released_at is None else lease.released_at.isoformat(),
        1 if lease.active and lease.released_at is None else None,
    )


def _insert_lease(connection: sqlite3.Connection, lease: ControllerLease) -> None:
    try:
        connection.execute(
            """
            INSERT INTO controller_leases (
                lease_id, slot_id, session_id, acquired_at, expires_at, released_at, active_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            _lease_values(lease),
        )
    except sqlite3.IntegrityError as exc:
        _raise_lease_conflict(lease.lease_id, exc)


def _update_lease(connection: sqlite3.Connection, lease: ControllerLease) -> None:
    values = _lease_values(lease)
    try:
        cursor = connection.execute(
            """
            UPDATE controller_leases
            SET slot_id = ?, session_id = ?, acquired_at = ?, expires_at = ?,
                released_at = ?, active_key = ?
            WHERE lease_id = ?
            """,
            (values[1], values[2], values[3], values[4], values[5], values[6], values[0]),
        )
    except sqlite3.IntegrityError as exc:
        _raise_lease_conflict(lease.lease_id, exc)
        return
    _require_update(
        connection,
        cursor,
        "controller_lease",
        lease.lease_id,
        "SELECT 1 FROM controller_leases WHERE lease_id = ?",
    )


def _raise_lease_conflict(lease_id: str, exc: sqlite3.IntegrityError) -> None:
    text = str(exc).lower()
    if "unique" in text or "primary key" in text:
        raise ControllerLeaseHeld(lease_id) from exc
    raise exc


def _session_values(
    session: AttachmentSession,
) -> tuple[str, str, str, str, str | None]:
    return (
        session.session_id,
        session.slot_id,
        session.expires_at.isoformat(),
        session.created_at.isoformat(),
        None if session.revoked_at is None else session.revoked_at.isoformat(),
    )


def _aware_time(label: str, value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise StorageError(f"{label} has no time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StorageError(f"{label} has an unreadable time") from exc
    if parsed.tzinfo is None:
        raise StorageError(f"{label} time has no timezone")
    return parsed


def _insert_operation_receipt(connection: sqlite3.Connection, receipt: OperationReceipt) -> None:
    try:
        connection.execute(
            """
            INSERT INTO operation_receipts (
                operation_id, command_name, fingerprint, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                receipt.operation_id,
                receipt.command_name,
                receipt.fingerprint,
                receipt.result_json,
                receipt.created_at.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise OperationReceiptTaken(receipt.operation_id) from exc


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


def _ticket_values(ticket: Ticket) -> tuple[str, str, str, str, str | None, str | None, str | None]:
    claimant = None if ticket.claimant is None else ticket.claimant.value
    review = None if ticket.review_result is None else ticket.review_result.value
    target = None if ticket.target_slot_id is None else ticket.target_slot_id.value
    return (
        ticket.ticket_id.value,
        ticket.issuer.value,
        ticket.content,
        ticket.state.value,
        claimant,
        review,
        target,
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
    if isinstance(item, SlotCredential):
        return item.slot_id
    if isinstance(item, AttachmentSession):
        return item.session_id
    if isinstance(item, ControllerLease):
        return item.lease_id
    return kind


def _label(kind: str) -> str:
    if kind == "lock":
        return "consumption lock"
    if kind == "file_lock":
        return "file lock"
    if kind == "slot_credential":
        return "slot credential"
    if kind == "attachment_session":
        return "attachment session"
    if kind == "controller_lease":
        return "controller lease"
    return kind


def _integrity(kind: str, key: str, exc: sqlite3.IntegrityError) -> StorageError:
    text = str(exc).lower()
    label = _label(kind)
    if "unique" in text or "primary key" in text:
        return DuplicateRecord(f"{label} {key} already exists")
    if "foreign key" in text:
        return StorageError(f"{label} {key} refers to a missing record")
    return StorageError(f"cannot store {label} {key}")


def _target_slot(ticket_id: str, value: object) -> SlotId | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return SlotId(text)
    except ValueError as exc:
        raise StorageError(f"ticket {ticket_id} has a blank target slot") from exc


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
