from __future__ import annotations

from src.main_graph.subgraphs.discovery.graph import (
    _route_after_clone,
    _route_after_inspect,
)
from src.main_graph.subgraphs.discovery.constants import (
    BUILD_PROJECT_CONTEXT,
    INSPECT_REPO,
    INSTALL_DEPS,
    INDEX_REPO,
)


def test_clone_error_skips_to_summary():
    assert (
        _route_after_clone({"discovery_error": "clone failed"}) == BUILD_PROJECT_CONTEXT
    )


def test_clone_success_goes_to_inspect():
    assert _route_after_clone({"discovery_error": None}) == INSPECT_REPO


def test_inspect_no_lock_goes_to_install():
    assert _route_after_inspect({"has_lock_file": False}) == INSTALL_DEPS


def test_inspect_lock_present_goes_to_index():
    assert _route_after_inspect({"has_lock_file": True}) == INDEX_REPO
