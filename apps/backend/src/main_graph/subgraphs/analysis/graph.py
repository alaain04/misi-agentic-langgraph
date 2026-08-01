from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.analysis.concern import route_concern
from src.main_graph.subgraphs.analysis.deepagent.nodes import (
    analysis_deepagent_node,
    backstop_dispatch_node,
    coverage_gate,
    route_after_coverage_gate,
)
from src.main_graph.subgraphs.analysis.nodes.run_direct_agents import run_direct_agents
from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import (
    save_analysis_result,
)
from src.main_graph.subgraphs.analysis.nodes.understand_concern import (
    understand_concern,
)
from src.main_graph.subgraphs.analysis.state import AnalysisState


def build_analysis_subgraph():
    builder = StateGraph(AnalysisState)

    builder.add_node("understand_concern", understand_concern)
    builder.add_node("run_direct_agents", run_direct_agents)
    builder.add_node("analysis_deepagent_node", analysis_deepagent_node)
    builder.add_node("coverage_gate", coverage_gate)
    builder.add_node("backstop_dispatch", backstop_dispatch_node)
    builder.add_node("save_analysis_result", save_analysis_result)

    builder.add_edge(START, "understand_concern")
    builder.add_conditional_edges(
        "understand_concern",
        route_concern,
        {"simple": "run_direct_agents", "complex": "analysis_deepagent_node"},
    )
    builder.add_edge("run_direct_agents", "save_analysis_result")
    builder.add_edge("analysis_deepagent_node", "coverage_gate")
    builder.add_conditional_edges("coverage_gate", route_after_coverage_gate)
    builder.add_edge("backstop_dispatch", "save_analysis_result")
    builder.add_edge("save_analysis_result", END)

    return builder.compile()


analysis_subgraph = build_analysis_subgraph()
