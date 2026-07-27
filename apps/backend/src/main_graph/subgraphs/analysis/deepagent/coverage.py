"""Deterministic coverage guarantee for the analysis deep agent (spec D5, D8).

The deep agent decides HOW to investigate; whether every direct dependency
got looked at by a package-scoped agent is never left to its judgment.
"""

from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY

WHOLE_TREE_AGENT_TYPES: set[str] = {"vulnerability_agent", "license_agent"}
"""Agents that scan the entire dependency tree in one run -- a second
dispatch adds no coverage and is capped to one run/job (D8), and they never
count toward direct-dependency coverage in compute_missing_direct_deps."""

PACKAGE_SCOPED_AGENT_TYPES: set[str] = set(REGISTRY) - WHOLE_TREE_AGENT_TYPES


def compute_missing_direct_deps(
    agent_calls: list[dict], direct_deps: list[str]
) -> list[str]:
    """Direct deps with no package-scoped AgentCallRecord covering them.

    agent_calls: list of AgentCallRecord.model_dump()-shaped dicts
    (agent_type, packages_to_focus, ...). Order of the returned list follows
    direct_deps, not agent_calls.
    """
    covered: set[str] = set()
    for call in agent_calls:
        if call.get("agent_type") in PACKAGE_SCOPED_AGENT_TYPES:
            covered.update(call.get("packages_to_focus") or [])
    return [dep for dep in direct_deps if dep not in covered]
