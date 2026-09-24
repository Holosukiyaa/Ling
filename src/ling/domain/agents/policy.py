"""Who may manage whom. Management is a template fact plus a strict level order."""

from __future__ import annotations

from ling.domain.agents.entities import Template


def can_manage(superior: Template, subordinate: Template) -> bool:
    """True only when the superior template lists the subordinate and outranks it.

    Peers cannot manage each other. A worker template has an empty management
    set. Checker is absent from the mentor declaration, so a mentor does not
    manage a checker even though both are level 2.
    """

    if not superior.level.outranks(subordinate.level):
        return False
    return subordinate.template_id in superior.manages
