from __future__ import annotations

from src.main_graph.subgraphs.analysis.deepagent.coverage import (
    PACKAGE_SCOPED_AGENT_TYPES,
    WHOLE_TREE_AGENT_TYPES,
    compute_missing_direct_deps,
)


def test_whole_tree_and_package_scoped_sets_partition_known_agent_types():
    from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY

    assert WHOLE_TREE_AGENT_TYPES == {"vulnerability_agent", "license_agent"}
    assert PACKAGE_SCOPED_AGENT_TYPES == set(REGISTRY) - WHOLE_TREE_AGENT_TYPES
    assert WHOLE_TREE_AGENT_TYPES.isdisjoint(PACKAGE_SCOPED_AGENT_TYPES)


def test_all_direct_deps_covered_returns_empty():
    agent_calls = [
        {
            "agent_type": "web_research_agent",
            "packages_to_focus": ["left-pad", "chalk"],
        },
        {"agent_type": "maintenance_agent", "packages_to_focus": ["left-pad"]},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["left-pad", "chalk"])
    assert missing == []


def test_some_direct_deps_uncovered_are_reported():
    agent_calls = [
        {"agent_type": "web_research_agent", "packages_to_focus": ["left-pad"]},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["left-pad", "chalk", "uuid"])
    assert missing == ["chalk", "uuid"]


def test_whole_tree_agent_calls_do_not_count_as_coverage():
    agent_calls = [
        {"agent_type": "vulnerability_agent", "packages_to_focus": []},
        {"agent_type": "license_agent", "packages_to_focus": []},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["left-pad", "chalk"])
    assert missing == ["left-pad", "chalk"]


def test_no_agent_calls_means_everything_missing():
    missing = compute_missing_direct_deps([], ["left-pad", "chalk"])
    assert missing == ["left-pad", "chalk"]


def test_missing_list_is_order_stable_by_direct_deps_order():
    agent_calls = [
        {"agent_type": "web_research_agent", "packages_to_focus": ["chalk"]},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["uuid", "chalk", "left-pad"])
    assert missing == ["uuid", "left-pad"]
