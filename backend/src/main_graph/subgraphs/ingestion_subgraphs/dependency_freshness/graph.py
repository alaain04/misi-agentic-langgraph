"""Dependency freshness subgraph builder."""

from langgraph.graph import END, START, StateGraph

from .constants import ANALYZE
from .nodes import analyze
from .state import DependencyFreshnessState

GRAPH_NAME = "dependency_freshness"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Detects outdated dependencies by comparing installed versions"
        " against the latest npm releases, flagging major gaps and deprecated packages"
    )


def build_dependency_freshness_subgraph() -> StateGraph:
    builder = StateGraph(DependencyFreshnessState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


dependency_freshness_subgraph = build_dependency_freshness_subgraph()
