from __future__ import annotations

from src.main_graph.subgraphs.analysis.graph import _after_conductor
from src.models.results import AnalysisConductorDecision, AgentDispatch


def _decision(**kwargs) -> AnalysisConductorDecision:
    defaults = dict(dispatches=[], finalize=False, reasoning="r")
    return AnalysisConductorDecision(**{**defaults, **kwargs})


def test_finalize_goes_to_save():
    state = {"conductor_decision": _decision(finalize=True)}
    assert _after_conductor(state) == "save_analysis_result"


def test_dispatches_go_to_dispatcher():
    d = AgentDispatch(domain="vulnerabilities", hypothesis="h",
                      packages_to_focus=[], agent_type="vulnerability_agent")
    state = {"conductor_decision": _decision(dispatches=[d])}
    assert _after_conductor(state) == "agent_dispatcher"


def test_empty_decision_finalizes():
    assert _after_conductor({}) == "save_analysis_result"
