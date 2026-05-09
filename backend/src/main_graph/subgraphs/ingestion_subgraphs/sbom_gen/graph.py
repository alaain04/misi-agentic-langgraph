"""Trivy scan subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_gen.state import SbomGenState

GRAPH_NAME = "sbom_gen"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Runs Trivy to generate an SBOM (CycloneDX) and scan"
        " for vulnerabilities and license data across all dependencies"
    )


def build_sbom_gen_subgraph():
    builder = StateGraph(SbomGenState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


sbom_gen_subgraph = build_sbom_gen_subgraph()
