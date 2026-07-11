from __future__ import annotations

from langgraph.types import Send

from src.main_graph.subgraphs.analysis.graph import _after_conductor
from src.models.results import AnalysisConductorDecision, AgentDispatch


def _decision(**kwargs) -> AnalysisConductorDecision:
    defaults = dict(dispatches=[], finalize=False, reasoning="r")
    return AnalysisConductorDecision(**{**defaults, **kwargs})


def test_finalize_goes_to_save():
    state = {"conductor_decision": _decision(finalize=True)}
    assert _after_conductor(state) == "save_analysis_result"


def test_dispatches_fan_out_via_send():
    d = AgentDispatch(domain="vulnerabilities", hypothesis="h",
                      packages_to_focus=[], agent_type="vulnerability_agent")
    state = {"conductor_decision": _decision(dispatches=[d]), "bundle_ids": []}
    result = _after_conductor(state)
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], Send)
    assert result[0].node == "domain_agent"


def test_multiple_dispatches_produce_multiple_sends():
    dispatches = [
        AgentDispatch(domain="vuln", hypothesis="h1", packages_to_focus=[], agent_type="vulnerability_agent"),
        AgentDispatch(domain="maint", hypothesis="h2", packages_to_focus=[], agent_type="maintenance_agent"),
    ]
    state = {"conductor_decision": _decision(dispatches=dispatches), "bundle_ids": []}
    result = _after_conductor(state)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(s, Send) and s.node == "domain_agent" for s in result)


def test_empty_decision_finalizes():
    assert _after_conductor({}) == "save_analysis_result"
