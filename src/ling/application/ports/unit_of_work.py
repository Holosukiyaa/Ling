"""Synchronous unit of work. One commit publishes every staged aggregate together."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from ling.application.ports.attachments import (
    AttachmentSessionRepository,
    SlotCredentialRepository,
)
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
