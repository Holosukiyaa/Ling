"""Template declarations and management policy."""

from __future__ import annotations

from ling.domain.agents import (
    CHECKER,
    INITIAL_TEMPLATES,
    MENTOR,
    WORKER,
    Level,
    Template,
    TemplateId,
    can_manage,
)


def test_initial_templates_are_data_with_declared_levels() -> None:
    catalog = {template.template_id.value: template for template in INITIAL_TEMPLATES}
    assert set(catalog) == {"mentor", "worker", "checker"}
    assert catalog["mentor"].level == Level(2)
    assert catalog["worker"].level == Level(1)
    assert catalog["checker"].level == Level(2)
    assert catalog["mentor"].manages == frozenset({TemplateId("worker")})
    assert catalog["worker"].manages == frozenset()
    assert catalog["checker"].manages == frozenset()
    assert type(MENTOR) is Template
    assert type(WORKER) is Template
    assert type(CHECKER) is Template


def test_mentor_manages_only_declared_lower_worker() -> None:
    assert can_manage(MENTOR, WORKER)
    assert not can_manage(MENTOR, CHECKER)
    assert not can_manage(MENTOR, MENTOR)


def test_peers_and_worker_cannot_manage() -> None:
    assert not can_manage(CHECKER, WORKER)
    assert not can_manage(CHECKER, MENTOR)
    assert not can_manage(WORKER, WORKER)
    assert not can_manage(WORKER, MENTOR)
    assert not can_manage(WORKER, CHECKER)


def test_same_level_listing_does_not_grant_management() -> None:
    peer = Template(
        template_id=TemplateId("lead"),
        level=Level(2),
        manages=frozenset({CHECKER.template_id, WORKER.template_id}),
    )
    assert not can_manage(peer, CHECKER)
    assert can_manage(peer, WORKER)

    chief = Template(
        template_id=TemplateId("chief"),
        level=Level(3),
        manages=frozenset({MENTOR.template_id}),
    )
    assert can_manage(chief, MENTOR)
    assert not can_manage(chief, WORKER)
