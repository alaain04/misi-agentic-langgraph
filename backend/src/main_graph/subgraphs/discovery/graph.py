"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    FETCH_REPOSITORY,
    PARSE_PACKAGE_FILES,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    fetch_repository,
    parse_package_files,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _after_fetch(state: DiscoveryState) -> str:
    """Short-circuit to summary on clone/manifest error."""
    return (
        BUILD_DEPENDENCY_SUMMARY
        if state.get("discovery_error")
        else PARSE_PACKAGE_FILES
    )


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(FETCH_REPOSITORY, fetch_repository)
    builder.add_node(PARSE_PACKAGE_FILES, parse_package_files)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, FETCH_REPOSITORY)
    builder.add_conditional_edges(
        FETCH_REPOSITORY,
        _after_fetch,
        [PARSE_PACKAGE_FILES, BUILD_DEPENDENCY_SUMMARY],
    )
    builder.add_edge(PARSE_PACKAGE_FILES, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
