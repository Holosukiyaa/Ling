"""Template declarations and slot identity. Templates are data, not subclasses."""

from __future__ import annotations

from dataclasses import dataclass

from ling.domain.agents.values import Level, SlotId, TemplateId


@dataclass(frozen=True, slots=True)
class Template:
    """One role declaration: level plus the template ids it may manage."""

    template_id: TemplateId
    level: Level
    manages: frozenset[TemplateId]


@dataclass(frozen=True, slots=True)
class Slot:
    """A template instance identified for domain rules. No presence or heartbeat."""

    slot_id: SlotId
    template: Template


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
