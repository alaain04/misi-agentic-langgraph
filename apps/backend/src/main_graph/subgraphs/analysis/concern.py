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
    is_valid: bool
    type: list[ConcernType]
    scope: ConcernScope
    packages: list[str] = Field(default_factory=list)
    requires_per_dependency_analysis: bool
    preferred_agents: list[str]


class ConcernDraft(BaseModel):
    is_valid: bool
    type: list[ConcernType]
    packages: list[str] = Field(default_factory=list)
    requires_per_dependency_analysis: bool


def packages_valid(packages: list[str], direct_deps: dict[str, str]) -> bool:
    return all(p in direct_deps for p in packages)


def invalid_concern() -> Concern:
    return Concern(
        is_valid=False,
        type=["other"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=[],
    )


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


def route_after_understand_concern(state: AnalysisState) -> str:
    """Gate on validity before anything else runs -- no point dispatching
    real agents (or even the deep agent) for input that was never a
    dependency-risk concern in the first place."""
    concern_dict = state["structured_concern"]
    return "valid" if concern_dict.get("is_valid", True) else "invalid"


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
