import operator
from typing import get_type_hints

from src.main_graph.state import MainState


def test_tool_results_has_add_reducer():
    hints = get_type_hints(MainState, include_extras=True)
    tool_results_hint = hints["tool_results"]
    metadata = getattr(tool_results_hint, "__metadata__", ())
    assert operator.add in metadata


def test_findings_has_add_reducer():
    hints = get_type_hints(MainState, include_extras=True)
    findings_hint = hints["findings"]
    metadata = getattr(findings_hint, "__metadata__", ())
    assert operator.add in metadata


def test_required_input_fields_present():
    hints = get_type_hints(MainState)
    for field in ("repo_url", "concern", "job_id", "autopilot"):
        assert field in hints
