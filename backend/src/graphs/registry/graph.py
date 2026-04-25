"""Registry subgraph — skeleton with mocked result."""

from langgraph.graph import END, START, StateGraph

from src.graphs.registry.constants import ANALYZE
from src.graphs.registry.nodes import analyze
from src.graphs.registry.state import RegistryState


def build_registry_subgraph():
    builder = StateGraph(RegistryState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


registry_subgraph = build_registry_subgraph()
