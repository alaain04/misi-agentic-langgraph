"""Cross-analyzer subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.cross_analyzer.constants import ANALYZE
from src.main_graph.subgraphs.cross_analyzer.nodes import analyze
from src.main_graph.subgraphs.cross_analyzer.state import CrossAnalyzerState


def build_cross_analyzer_subgraph():
    builder = StateGraph(CrossAnalyzerState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


cross_analyzer_subgraph = build_cross_analyzer_subgraph()
