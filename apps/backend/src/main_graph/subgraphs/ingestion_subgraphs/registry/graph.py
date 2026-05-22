"""Registry subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.registry.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.registry.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState

GRAPH_NAME = "registry"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Fetches npm registry metadata for one dependency:"
        " last publish date, weekly downloads, deprecation status, and maintainer count"
    )


def build_registry_subgraph() -> StateGraph:
    builder = StateGraph(RegistryState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


registry_subgraph = build_registry_subgraph()
