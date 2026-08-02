from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.main_graph.subgraphs.analysis.deepagent.coverage import WHOLE_TREE_AGENT_TYPES
from src.main_graph.subgraphs.analysis.state import AnalysisState

ConcernType = Literal[
    "vulnerability", "license", "maintenance", "supply_chain", "web_research", "other"
]
ConcernScope = Literal["all_dependencies", "specific_packages"]


class Concern(BaseModel):
    type: list[ConcernType]
    scope: ConcernScope
    packages: list[str] = Field(default_factory=list)
    requires_per_dependency_analysis: bool
    preferred_agents: list[str]


SIMPLE_CONCERN_TYPES = {"vulnerability", "license"}


def is_simple(concern: Concern) -> bool:
    return (
        set(concern.type) <= SIMPLE_CONCERN_TYPES
        and not concern.requires_per_dependency_analysis
        and concern.scope == "all_dependencies"
    )


def route_concern(state: AnalysisState) -> str:
    concern = Concern(**state["structured_concern"])
    return "simple" if is_simple(concern) else "complex"


def whole_tree_agents(concern: Concern) -> list[str]:
    """Whole-tree agents (vulnerability_agent/license_agent) relevant to this
    concern that are safe to run deterministically regardless of whether the
    concern also has non-whole-tree work. These agents ignore
    packages_to_focus and always return complete, per-dependency findings in
    a single deterministic pass, so there is nothing a DeepAgent
    investigation could add for them specifically."""
    if concern.scope != "all_dependencies":
        return []
    return [a for a in concern.preferred_agents if a in WHOLE_TREE_AGENT_TYPES]
