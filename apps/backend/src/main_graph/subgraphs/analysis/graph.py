from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.analysis.deepagent.nodes import (
    analysis_deepagent_node,
    backstop_dispatch_node,
    coverage_gate,
    route_after_coverage_gate,
)
from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import (
    save_analysis_result,
)
from src.main_graph.subgraphs.analysis.state import AnalysisState


def build_analysis_subgraph():
    builder = StateGraph(AnalysisState)

    builder.add_node("analysis_deepagent_node", analysis_deepagent_node)
    builder.add_node("coverage_gate", coverage_gate)
    builder.add_node("backstop_dispatch", backstop_dispatch_node)
    builder.add_node("save_analysis_result", save_analysis_result)

    builder.add_edge(START, "analysis_deepagent_node")
    builder.add_edge("analysis_deepagent_node", "coverage_gate")
    builder.add_conditional_edges("coverage_gate", route_after_coverage_gate)
    builder.add_edge("backstop_dispatch", "save_analysis_result")
    builder.add_edge("save_analysis_result", END)

    return builder.compile()


analysis_subgraph = build_analysis_subgraph()
