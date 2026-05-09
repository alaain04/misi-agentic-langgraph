"""Vulnerabilities subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.constants import (
    ANALYZE,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.state import (
    VulnerabilitiesState,
)

GRAPH_NAME = "vulnerabilities"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Identifies known CVEs, security advisories,"
        " and vulnerable dependency versions across direct and transitive dependencies"
    )


def build_vulnerabilities_subgraph() -> StateGraph:
    builder = StateGraph(VulnerabilitiesState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


vulnerabilities_subgraph = build_vulnerabilities_subgraph()
