"""Runtime subgraph — skeleton with mocked result."""

from langgraph.graph import END, START, StateGraph

from src.graphs.runtime.constants import ANALYZE
from src.graphs.runtime.nodes import analyze
from src.graphs.runtime.state import RuntimeState


def build_runtime_subgraph():
    builder = StateGraph(RuntimeState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


runtime_subgraph = build_runtime_subgraph()
