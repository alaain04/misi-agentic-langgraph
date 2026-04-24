"""ProjectDiscovery subgraph builder."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from src.graphs.project_discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    DETECT_MANIFEST_FILES,
    FETCH_REPOSITORY,
    PARSE_PACKAGE_FILES,
)
from src.graphs.project_discovery.nodes import (
    build_dependency_summary,
    detect_manifest_files,
    fetch_repository,
    parse_package_files,
)
from src.graphs.project_discovery.routes import after_fetch_repository
from src.graphs.project_discovery.state import DiscoveryState

_HTTP_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)


def build_project_discovery_subgraph() -> StateGraph:
    """
    Return a compiled ProjectDiscovery subgraph.

    Graph topology
    ──────────────
    START
      └─► fetch_repository
            ├─► (error) ──────────────────────────► build_dependency_summary
            └─► detect_manifest_files
                  └─► parse_package_files
                        └─► build_dependency_summary
                              └─► END

    All HTTP-calling nodes carry a RetryPolicy for transient failures.
    """
    builder = StateGraph(DiscoveryState)

    builder.add_node(FETCH_REPOSITORY, fetch_repository, retry_policy=_HTTP_RETRY)
    builder.add_node(
        DETECT_MANIFEST_FILES, detect_manifest_files, retry_policy=_HTTP_RETRY
    )
    builder.add_node(PARSE_PACKAGE_FILES, parse_package_files, retry_policy=_HTTP_RETRY)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, FETCH_REPOSITORY)
    builder.add_conditional_edges(
        FETCH_REPOSITORY,
        after_fetch_repository,
        {
            DETECT_MANIFEST_FILES: DETECT_MANIFEST_FILES,
            BUILD_DEPENDENCY_SUMMARY: BUILD_DEPENDENCY_SUMMARY,
        },
    )
    builder.add_edge(DETECT_MANIFEST_FILES, PARSE_PACKAGE_FILES)
    builder.add_edge(PARSE_PACKAGE_FILES, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


project_discovery_subgraph = build_project_discovery_subgraph()
