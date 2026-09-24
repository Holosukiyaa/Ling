"""In-process ticket state machine. Automatic transitions are disabled."""

from __future__ import annotations

from dataclasses import dataclass

from transitions import Machine
from transitions.core import MachineError

from ling.domain.errors import InvalidTransition, UnknownEvent
from ling.domain.tickets.states import TicketState

KNOWN_EVENTS: frozenset[str] = frozenset(
    {"claim", "submit", "accept", "reject", "consume"}
)

_SOURCES: dict[str, TicketState | tuple[TicketState, ...]] = {
    "claim": TicketState.QUEUED,
    "submit": TicketState.CLAIMED,
    "accept": TicketState.SUBMITTED,
    "reject": TicketState.SUBMITTED,
    "consume": (TicketState.ACCEPTED, TicketState.REJECTED),
}


@dataclass
class _MachineModel:
    """Attribute bag owned by transitions. Ticket does not expose it."""

    status: str = TicketState.QUEUED.value


def _transition_table() -> list[dict[str, object]]:
    table: list[dict[str, object]] = []
    for event, source in _SOURCES.items():
        if isinstance(source, tuple):
            source_value: str | list[str] = [item.value for item in source]
            dest = TicketState.CONSUMED.value
        else:
            source_value = source.value
            dest = {
                "claim": TicketState.CLAIMED.value,
                "submit": TicketState.SUBMITTED.value,
                "accept": TicketState.ACCEPTED.value,
                "reject": TicketState.REJECTED.value,
            }[event]
        table.append({"trigger": event, "source": source_value, "dest": dest})
    return table


class TicketMachine:
    """Closed transitions graph. `auto_transitions` stays off for the process."""

    def __init__(self) -> None:
        self._model = _MachineModel()
        self.machine = Machine(
            model=self._model,
            states=[state.value for state in TicketState],
            initial=TicketState.QUEUED.value,
            transitions=_transition_table(),
            auto_transitions=False,
            send_event=False,
            model_attribute="status",
        )

    @property
    def auto_transitions(self) -> bool:
        return bool(self.machine.auto_transitions)

    @property
    def state(self) -> TicketState:
        return TicketState(self._model.status)

    def fire(self, event: str) -> TicketState:
        """Move along `event` or raise a Ling domain error without changing state."""

        if event not in KNOWN_EVENTS:
            raise UnknownEvent(f"unknown ticket event: {event}")
        previous = self.state
        try:
            self._model.trigger(event)
        except MachineError as exc:
            raise InvalidTransition(
                f"cannot {event} from {previous.value}"
            ) from exc
        except Exception as exc:
            raise InvalidTransition(
                f"cannot {event} from {previous.value}"
            ) from exc
        if self.state != previous:
            return self.state
        raise InvalidTransition(f"cannot {event} from {previous.value}")
        return self.state
