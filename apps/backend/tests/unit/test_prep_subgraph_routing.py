from __future__ import annotations

from src.main_graph.subgraphs.discovery.constants import (
    INDEX_REPO,
    INSPECT_REPO,
    INSTALL_DEPS,
    SAVE_PREP_RESULT,
)
from src.main_graph.subgraphs.discovery.graph import (
    _route_after_clone,
    _route_after_inspect,
)


def test_clone_error_skips_to_save():
    assert (
        _route_after_clone({"discovery_error": "clone failed"}) == SAVE_PREP_RESULT
    )


def test_clone_success_goes_to_inspect():
    assert _route_after_clone({"discovery_error": None}) == INSPECT_REPO


def test_inspect_no_lock_goes_to_install():
    assert _route_after_inspect({"has_lock_file": False}) == INSTALL_DEPS


def test_inspect_lock_present_goes_to_index():
    assert _route_after_inspect({"has_lock_file": True}) == INDEX_REPO
