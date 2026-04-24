"""ProjectDiscovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.graphs.project_discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    PARSE_PACKAGE_FILES,
)
from src.graphs.project_discovery.nodes import (
    build_dependency_summary,
    parse_package_files,
)
from src.graphs.project_discovery.state import DiscoveryState


def build_project_discovery_subgraph() -> StateGraph:
    """
    Return a compiled ProjectDiscovery subgraph.

    Graph topology
    ──────────────
    START
      └─► parse_package_files
            └─► build_dependency_summary
                  └─► END
    """
    builder = StateGraph(DiscoveryState)

    builder.add_node(PARSE_PACKAGE_FILES, parse_package_files)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, PARSE_PACKAGE_FILES)
    builder.add_edge(PARSE_PACKAGE_FILES, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


project_discovery_subgraph = build_project_discovery_subgraph()
