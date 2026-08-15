from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .constants import (
    CLONE_REPO,
    DETECT_NODE_ENV,
    INSTALL_DEPS,
    SAVE_PREP_RESULT,
)
from .nodes import (
    clone_repo,
    detect_node_environment,
    install_deps,
    save_prep_result,
)
from .state import DiscoveryState


def _route_after_clone(state: DiscoveryState) -> str:
    return SAVE_PREP_RESULT if state.get("discovery_error") else DETECT_NODE_ENV


def _route_after_inspect(state: DiscoveryState) -> str:
    return INSTALL_DEPS if not state.get("has_lock_file") else SAVE_PREP_RESULT


def build_discovery_subgraph() -> CompiledStateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPO, clone_repo)
    builder.add_node(DETECT_NODE_ENV, detect_node_environment)
    builder.add_node(INSTALL_DEPS, install_deps)
    builder.add_node(SAVE_PREP_RESULT, save_prep_result)

    builder.add_edge(START, CLONE_REPO)
    builder.add_conditional_edges(CLONE_REPO, _route_after_clone)
    builder.add_conditional_edges(DETECT_NODE_ENV, _route_after_inspect)
    builder.add_edge(INSTALL_DEPS, SAVE_PREP_RESULT)
    builder.add_edge(SAVE_PREP_RESULT, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
