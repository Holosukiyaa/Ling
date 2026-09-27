"""Ports that infrastructure may implement. Synchronous protocols only."""

from ling.application.ports.attachments import (
    AttachmentSession,
    AttachmentSessionRepository,
    SlotCredential,
    SlotCredentialRepository,
)
from ling.application.ports.clock import Clock
from ling.application.ports.id_generator import IdGenerator
from ling.application.ports.leases import (
    ControllerLease,
    ControllerLeaseHeld,
    ControllerLeaseRepository,
)
from ling.application.ports.observability import (
    RUNTIME_EVENT_SCHEMA,
    NullRuntimeEventSink,
    RuntimeEvent,
    RuntimeEventSink,
)
from ling.application.ports.observer import NullRuntimeObserver, RuntimeObserver
from ling.application.ports.repositories import (
    ConsumptionLockRepository,
    FileLockRepository,
    SlotRepository,
    TicketRepository,
)
from ling.application.ports.unit_of_work import UnitOfWork

__all__ = [
    "AttachmentSession",
    "AttachmentSessionRepository",
    "Clock",
    "ConsumptionLockRepository",
    "ControllerLease",
    "ControllerLeaseHeld",
    "ControllerLeaseRepository",
    "FileLockRepository",
    "IdGenerator",
    "NullRuntimeEventSink",
    "NullRuntimeObserver",
    "RUNTIME_EVENT_SCHEMA",
    "RuntimeEvent",
    "RuntimeEventSink",
    "RuntimeObserver",
    "SlotCredential",
    "SlotCredentialRepository",
    "SlotRepository",
    "TicketRepository",
    "UnitOfWork",
]
