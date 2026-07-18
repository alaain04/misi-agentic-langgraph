from __future__ import annotations

from src.main_graph.subgraphs.report.graph import _after_conductor
from src.models.results import ReportConductorDecision
from src.models.conductor import ToolCall


def _decision(**kwargs) -> ReportConductorDecision:
    defaults = dict(tool_calls=[], finalize=False, reasoning="r")
    return ReportConductorDecision(**{**defaults, **kwargs})


def test_finalize_goes_to_save():
    assert (
        _after_conductor({"conductor_decision": _decision(finalize=True)})
        == "save_report_result"
    )


def test_tool_calls_go_to_runner():
    tc = ToolCall(tool="web_search", args={"query": "q"}, reason="r")
    assert (
        _after_conductor({"conductor_decision": _decision(tool_calls=[tc])})
        == "report_tool_runner"
    )


def test_empty_decision_finalizes():
    assert _after_conductor({}) == "save_report_result"
