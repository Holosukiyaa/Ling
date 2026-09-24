from __future__ import annotations

import ling
import ling.application
import ling.application.ports
import ling.bootstrap
import ling.domain
import ling.infrastructure
import ling.interfaces
from tests.architecture.import_direction import check_source, check_tree


def test_package_imports() -> None:
    assert ling.__version__ == "0.0.0"
    assert ling.domain.__doc__
    assert ling.application.ports.__doc__
    assert ling.infrastructure.__doc__
    assert ling.interfaces.__doc__
    assert ling.bootstrap.__doc__


def test_source_tree_obeys_import_direction() -> None:
    assert check_tree() == []


def test_domain_cannot_import_outer_layers() -> None:
    source = "import ling.application\nfrom ling.infrastructure import persistence\n"
    found = check_source("ling.domain.tickets", source)
    assert len(found) == 2


def test_application_cannot_import_adapters_or_sdks() -> None:
    source = "import sqlite3\nimport ag\nfrom ling.bootstrap import container\nimport httpx\n"
    found = check_source("ling.application.commands", source)
    assert len(found) == 4


def test_interfaces_cannot_import_infrastructure() -> None:
    source = "from ling.infrastructure.persistence import sqlite\n"
    found = check_source("ling.interfaces.mcp", source)
    assert found


def test_infrastructure_is_limited_to_ports_and_domain() -> None:
    bad = "from ling.application.commands import dispatch\n"
    good = "from ling.application.ports import repositories\nfrom ling.domain.tickets import entities\n"
    assert check_source("ling.infrastructure.persistence.sqlite", bad)
    assert check_source("ling.infrastructure.persistence.sqlite", good) == []


def test_only_bootstrap_may_wire_concrete_modules() -> None:
    source = "from ling.infrastructure.persistence.sqlite import connection\n"
    assert check_source("ling.bootstrap.container", source) == []
    assert check_source("ling.application", source)


def test_relative_import_stays_inside_the_layer() -> None:
    assert check_source("ling.domain.agents", "from . import entities\n") == []
    assert check_source("ling.domain.agents", "from ..application import commands\n")


def test_shared_package_names_are_rejected() -> None:
    assert check_source("ling.utils.helpers", "x = 1\n")
