from __future__ import annotations

from src.main_graph.subgraphs.discovery.constants import (
    DETECT_NODE_ENV,
    INSTALL_DEPS,
    SAVE_PREP_RESULT,
)
from src.main_graph.subgraphs.discovery.graph import (
    _route_after_clone,
    _route_after_inspect,
)


def test_clone_error_skips_to_save():
    assert _route_after_clone({"discovery_error": "clone failed"}) == SAVE_PREP_RESULT


def test_clone_success_goes_to_inspect():
    assert _route_after_clone({"discovery_error": None}) == DETECT_NODE_ENV


def test_inspect_no_lock_goes_to_install():
    assert _route_after_inspect({"lockfile_generated": ""}) == INSTALL_DEPS


def test_inspect_lock_present_goes_to_save():
    assert (
        _route_after_inspect({"lockfile_generated": "package-lock.json"})
        == SAVE_PREP_RESULT
    )
