"""Synchronous unit of work. One commit publishes every staged aggregate together."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from ling.application.ports.attachments import (
    AttachmentSessionLookup,
    AttachmentSessionRepository,
    SlotCredentialRepository,
)
from ling.application.ports.leases import ControllerLeaseRepository
from ling.application.ports.operation_receipts import OperationReceiptRepository
from ling.application.ports.repositories import (
    ConsumptionLockRepository,
    FileLockRepository,
    SlotRepository,
    TicketRepository,
)


class UnitOfWork(Protocol):
    """Transaction boundary for slots, tickets, locks, and operation receipts.

    Implementations must not expose a database driver. `commit` is the only
    point at which staged saves become durable. `rollback` drops the stage.
    """

    slots: SlotRepository
    tickets: TicketRepository
    consumption_locks: ConsumptionLockRepository
    file_locks: FileLockRepository
    operation_receipts: OperationReceiptRepository
    credentials: SlotCredentialRepository
    attachment_sessions: AttachmentSessionRepository
    controller_leases: ControllerLeaseRepository

    def commit(self) -> None:
        """Publish staged aggregates in the order they were saved."""

    def rollback(self) -> None:
        """Drop staged aggregates. Committed state stays as it was."""

    def __enter__(self) -> Self:
        """Begin the unit of work."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back when the block raised. Successful blocks commit explicitly."""


class ReadOnlyUnitOfWork(Protocol):
    """One consistent read snapshot. It cannot stage or publish writes.

    Dashboard reads slots, tickets, consumption locks, and file locks here.
    Dashboard attachment resolution reads one session and its slot here too.
    Operation receipts are not on this port. `close` ends the snapshot.
    """

    slots: SlotRepository
    tickets: TicketRepository
    consumption_locks: ConsumptionLockRepository
    file_locks: FileLockRepository
    attachment_sessions: AttachmentSessionLookup

    def rollback(self) -> None:
        """End the snapshot. Nothing is published."""

    def close(self) -> None:
        """Finish the snapshot and release its connection."""

    def __enter__(self) -> Self:
        """Open the snapshot."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End the snapshot when the block leaves, including after an error."""
