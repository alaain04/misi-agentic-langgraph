"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    CLONE_REPOSITORY,
    GENERATE_SBOM,
    INSPECTOR_AGENT,
    LOCK_GENERATOR_AGENT,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    clone_repository,
    generate_sbom,
    inspector_agent,
    lock_generator_agent,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _after_clone(state: DiscoveryState) -> str:
    return BUILD_DEPENDENCY_SUMMARY if state.get("discovery_error") else INSPECTOR_AGENT


def _after_inspector(state: DiscoveryState) -> str:
    if state.get("discovery_error"):
        return BUILD_DEPENDENCY_SUMMARY
    if state.get("lock_file_missing"):
        return LOCK_GENERATOR_AGENT
    return GENERATE_SBOM


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPOSITORY, clone_repository)
    builder.add_node(INSPECTOR_AGENT, inspector_agent)
    builder.add_node(LOCK_GENERATOR_AGENT, lock_generator_agent)
    builder.add_node(GENERATE_SBOM, generate_sbom)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, CLONE_REPOSITORY)
    builder.add_conditional_edges(
        CLONE_REPOSITORY, _after_clone, [INSPECTOR_AGENT, BUILD_DEPENDENCY_SUMMARY]
    )
    builder.add_conditional_edges(
        INSPECTOR_AGENT,
        _after_inspector,
        [LOCK_GENERATOR_AGENT, GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY],
    )
    builder.add_edge(LOCK_GENERATOR_AGENT, GENERATE_SBOM)
    builder.add_edge(GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
