"""License compliance subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.constants import (
    ANALYZE,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.nodes import (
    analyze,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.state import (
    LicenseComplianceState,
)

GRAPH_NAME = "license_compliance"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Checks dependency licenses for conflicts,"
        " restrictions, and compliance with project policies"
    )


def build_license_compliance_subgraph() -> StateGraph:
    builder = StateGraph(LicenseComplianceState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


license_compliance_subgraph = build_license_compliance_subgraph()
