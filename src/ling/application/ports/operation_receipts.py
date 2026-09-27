"""Port for successful operation receipts. These are not domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class OperationReceiptTaken(Exception):
    """The operation id was committed by another unit of work."""

    def __init__(self, operation_id: str) -> None:
        super().__init__(operation_id)
        self.operation_id = operation_id


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    """One committed success, stored so a retry can return it unchanged."""

    operation_id: str
    command_name: str
    fingerprint: str
    result_json: str
    created_at: datetime


class OperationReceiptRepository(Protocol):
    """Load and stage receipts inside the current unit of work."""

    def get(self, operation_id: str) -> OperationReceipt | None:
        """Return the staged or stored receipt, or None."""

    def save(self, receipt: OperationReceipt) -> None:
        """Stage `receipt` until the unit of work commits."""
