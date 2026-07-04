"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    CLONE_REPO,
    GENERATE_SBOM,
    INSPECT_REPO,
    INSTALL_DEPS,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    clone_repo,
    generate_sbom,
    inspect_repo,
    install_deps,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _route_after_clone(state: DiscoveryState) -> str:
    return BUILD_DEPENDENCY_SUMMARY if state.get("discovery_error") else INSPECT_REPO


def _route_after_inspect(state: DiscoveryState) -> str:
    return GENERATE_SBOM if state.get("has_lock_file") else INSTALL_DEPS


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPO, clone_repo)
    builder.add_node(INSPECT_REPO, inspect_repo)
    builder.add_node(INSTALL_DEPS, install_deps)
    builder.add_node(GENERATE_SBOM, generate_sbom)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, CLONE_REPO)
    builder.add_conditional_edges(CLONE_REPO, _route_after_clone)
    builder.add_conditional_edges(INSPECT_REPO, _route_after_inspect)
    builder.add_edge(INSTALL_DEPS, GENERATE_SBOM)
    builder.add_edge(GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
