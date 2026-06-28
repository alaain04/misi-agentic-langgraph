"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    DISCOVERY_ORCHESTRATOR,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    discovery_orchestrator,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(DISCOVERY_ORCHESTRATOR, discovery_orchestrator)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, DISCOVERY_ORCHESTRATOR)
    builder.add_edge(DISCOVERY_ORCHESTRATOR, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
