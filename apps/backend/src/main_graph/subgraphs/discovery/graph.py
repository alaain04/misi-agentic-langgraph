from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_PROJECT_CONTEXT,
    CLONE_REPO,
    INDEX_REPO,
    INSPECT_REPO,
    INSTALL_DEPS,
    SAVE_PREP_RESULT,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_project_context,
    clone_repo,
    index_repository,
    inspect_repo,
    install_deps,
    save_prep_result,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _route_after_clone(state: DiscoveryState) -> str:
    return BUILD_PROJECT_CONTEXT if state.get("discovery_error") else INSPECT_REPO


def _route_after_inspect(state: DiscoveryState) -> str:
    return INSTALL_DEPS if not state.get("has_lock_file") else INDEX_REPO


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPO, clone_repo)
    builder.add_node(INSPECT_REPO, inspect_repo)
    builder.add_node(INSTALL_DEPS, install_deps)
    builder.add_node(INDEX_REPO, index_repository)
    builder.add_node(BUILD_PROJECT_CONTEXT, build_project_context)
    builder.add_node(SAVE_PREP_RESULT, save_prep_result)

    builder.add_edge(START, CLONE_REPO)
    builder.add_conditional_edges(CLONE_REPO, _route_after_clone)
    builder.add_conditional_edges(INSPECT_REPO, _route_after_inspect)
    builder.add_edge(INSTALL_DEPS, INDEX_REPO)
    builder.add_edge(INDEX_REPO, BUILD_PROJECT_CONTEXT)
    builder.add_edge(BUILD_PROJECT_CONTEXT, SAVE_PREP_RESULT)
    builder.add_edge(SAVE_PREP_RESULT, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
