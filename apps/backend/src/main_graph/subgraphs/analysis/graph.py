from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.main_graph.subgraphs.analysis.agents.registry import AGENT_REGISTRY
from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import analysis_conductor
from src.main_graph.subgraphs.analysis.nodes.domain_agent import domain_agent
from src.main_graph.subgraphs.analysis.nodes.evidence_collector import evidence_collector
from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import save_analysis_result
from src.main_graph.subgraphs.analysis.state import AnalysisState


def _after_conductor(state: AnalysisState):
    """
    Route from analysis_conductor.

    Returns a list[Send] to fan out to parallel domain_agent invocations,
    or the string "save_analysis_result" to finalize.

    Note: Send-based fan-out must happen from a conditional EDGE function,
    not from a node — LangGraph 1.x does not support list[Send] node returns.
    """
    decision = state.get("conductor_decision")
    if not decision or decision.finalize or not decision.dispatches:
        return "save_analysis_result"

    sends = []
    for dispatch in decision.dispatches:
        agent_type = (
            dispatch.agent_type if dispatch.agent_type in AGENT_REGISTRY else "web_research_agent"
        )
        dispatch_dict = dispatch.model_dump()
        dispatch_dict["agent_type"] = agent_type
        sends.append(
            Send("domain_agent", {
                **state,
                "current_dispatch": dispatch_dict,
                "bundle_ids": [],
            })
        )
    return sends


def build_analysis_subgraph():
    builder = StateGraph(AnalysisState)

    builder.add_node("analysis_conductor", analysis_conductor)
    builder.add_node("domain_agent", domain_agent)
    builder.add_node("evidence_collector", evidence_collector)
    builder.add_node("save_analysis_result", save_analysis_result)

    builder.add_edge(START, "analysis_conductor")
    # _after_conductor returns list[Send] (fan-out) or "save_analysis_result" (finalize)
    builder.add_conditional_edges("analysis_conductor", _after_conductor)
    builder.add_edge("domain_agent", "evidence_collector")
    builder.add_edge("evidence_collector", "analysis_conductor")
    builder.add_edge("save_analysis_result", END)

    return builder.compile()


analysis_subgraph = build_analysis_subgraph()
