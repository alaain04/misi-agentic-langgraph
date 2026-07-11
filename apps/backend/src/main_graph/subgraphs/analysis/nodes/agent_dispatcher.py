from __future__ import annotations

from langgraph.types import Send

from src.main_graph.subgraphs.analysis.agents.registry import AGENT_REGISTRY
from src.main_graph.subgraphs.analysis.state import AnalysisState


def agent_dispatcher(state: AnalysisState) -> list[Send]:
    """Fan-out node: sends one domain_agent invocation per dispatch."""
    decision = state.get("conductor_decision")
    if not decision or not decision.dispatches:
        return []

    sends = []
    for dispatch in decision.dispatches:
        agent_type = dispatch.agent_type if dispatch.agent_type in AGENT_REGISTRY else "web_research_agent"
        sends.append(Send("domain_agent", {
            **state,
            "current_dispatch": dispatch.model_dump(),
            "bundle_ids": [],  # reset accumulator for this branch
        }))
    return sends
