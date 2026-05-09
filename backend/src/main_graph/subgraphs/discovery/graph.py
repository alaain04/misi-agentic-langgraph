"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    CLONE_REPOSITORY,
    GENERATE_SBOM,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    clone_repository,
    generate_sbom,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _after_fetch(state: DiscoveryState) -> str:
    return (
        BUILD_DEPENDENCY_SUMMARY
        if state.get("discovery_error")
        else GENERATE_SBOM
    )


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPOSITORY, clone_repository)
    builder.add_node(GENERATE_SBOM, generate_sbom)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, CLONE_REPOSITORY)
    builder.add_conditional_edges(
        CLONE_REPOSITORY,
        _after_fetch,
        [GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY],
    )
    builder.add_edge(GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
