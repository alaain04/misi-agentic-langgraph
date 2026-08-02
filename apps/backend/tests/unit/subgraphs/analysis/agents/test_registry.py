from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.registry import (
    REGISTRY,
    agents_for_types,
)


def test_every_registered_agent_declares_concern_types():
    for agent_type, cls in REGISTRY.items():
        assert cls.concern_types, f"{agent_type} has no concern_types"


def test_agents_for_types_single_type():
    assert agents_for_types(["vulnerability"]) == ["vulnerability_agent"]


def test_agents_for_types_other_and_web_research_both_resolve_to_web_research_agent():
    assert agents_for_types(["other"]) == ["web_research_agent"]
    assert agents_for_types(["web_research"]) == ["web_research_agent"]


def test_agents_for_types_multiple_types_deduped_in_registry_order():
    assert agents_for_types(["license", "vulnerability"]) == [
        "vulnerability_agent",
        "license_agent",
    ]


def test_agents_for_types_unknown_type_returns_empty():
    assert agents_for_types(["not_a_real_type"]) == []


def test_agents_for_types_empty_input_returns_empty():
    assert agents_for_types([]) == []
