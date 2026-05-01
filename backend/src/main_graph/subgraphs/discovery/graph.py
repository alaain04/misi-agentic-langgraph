"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    PARSE_PACKAGE_FILES,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    parse_package_files,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(PARSE_PACKAGE_FILES, parse_package_files)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, PARSE_PACKAGE_FILES)
    builder.add_edge(PARSE_PACKAGE_FILES, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
