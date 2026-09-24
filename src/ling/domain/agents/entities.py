"""Template declarations and slot identity. Templates are data, not subclasses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ling.domain.agents.values import Level, SlotId, TemplateId


@dataclass(frozen=True, slots=True)
class Template:
    """One role declaration: level plus the template ids it may manage."""

    template_id: TemplateId
    level: Level
    manages: frozenset[TemplateId]


@dataclass(frozen=True, slots=True)
class Slot:
    """A template instance. Presence is Ling's own online flag and heartbeat time.

    There is no external agent id on this object. Callers are addressed by `slot_id`.
    """

    slot_id: SlotId
    template: Template
    online: bool = False
    last_heartbeat_at: datetime | None = None

    def record_heartbeat(self, at: datetime) -> Slot:
        """Return this slot marked online at `at`. `at` must carry a timezone."""

        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError("heartbeat time must be timezone-aware")
        return Slot(
            slot_id=self.slot_id,
            template=self.template,
            online=True,
            last_heartbeat_at=at,
        )


MENTOR_ID = TemplateId("mentor")
WORKER_ID = TemplateId("worker")
CHECKER_ID = TemplateId("checker")

WORKER = Template(template_id=WORKER_ID, level=Level(1), manages=frozenset())
MENTOR = Template(
    template_id=MENTOR_ID,
    level=Level(2),
    manages=frozenset({WORKER_ID}),
)
CHECKER = Template(template_id=CHECKER_ID, level=Level(2), manages=frozenset())

INITIAL_TEMPLATES: tuple[Template, ...] = (MENTOR, WORKER, CHECKER)


def template_catalog() -> dict[TemplateId, Template]:
    """The three initial declarations keyed by template id."""

    return {template.template_id: template for template in INITIAL_TEMPLATES}
