from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import analysis_conductor
from src.main_graph.subgraphs.analysis.nodes.agent_dispatcher import agent_dispatcher
from src.main_graph.subgraphs.analysis.nodes.domain_agent import domain_agent
from src.main_graph.subgraphs.analysis.nodes.evidence_collector import evidence_collector
from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import save_analysis_result
from src.main_graph.subgraphs.analysis.state import AnalysisState


def _after_conductor(state: AnalysisState) -> str:
    """Route to agent_dispatcher (fan-out) or save_analysis_result (finalize)."""
    decision = state.get("conductor_decision")
    if not decision or decision.finalize:
        return "save_analysis_result"
    if decision.dispatches:
        return "agent_dispatcher"
    return "save_analysis_result"


def build_analysis_subgraph():
    builder = StateGraph(AnalysisState)

    builder.add_node("analysis_conductor", analysis_conductor)
    # agent_dispatcher is a node that returns list[Send] — LangGraph fans out to domain_agent
    builder.add_node("agent_dispatcher", agent_dispatcher)
    builder.add_node("domain_agent", domain_agent)
    builder.add_node("evidence_collector", evidence_collector)
    builder.add_node("save_analysis_result", save_analysis_result)

    builder.add_edge(START, "analysis_conductor")
    builder.add_conditional_edges("analysis_conductor", _after_conductor,
                                  ["agent_dispatcher", "save_analysis_result"])
    builder.add_edge("domain_agent", "evidence_collector")
    builder.add_edge("evidence_collector", "analysis_conductor")
    builder.add_edge("save_analysis_result", END)

    return builder.compile()


analysis_subgraph = build_analysis_subgraph()
