"""Ports that infrastructure may implement. Synchronous protocols only."""

from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.observer import NullRuntimeObserver, RuntimeObserver
from ling.application.ports.repositories import (
    ConsumptionLockRepository,
    FileLockRepository,
    SlotRepository,
    TicketRepository,
)
from ling.application.ports.unit_of_work import UnitOfWork

__all__ = [
    "Clock",
    "ConsumptionLockRepository",
    "FileLockRepository",
    "IdGenerator",
    "NullRuntimeObserver",
    "RuntimeObserver",
    "SlotRepository",
    "TicketRepository",
    "UnitOfWork",
]
