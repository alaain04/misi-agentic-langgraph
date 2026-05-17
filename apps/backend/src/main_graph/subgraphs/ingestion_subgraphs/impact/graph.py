"""Impact subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.impact.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState

GRAPH_NAME = "impact"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Analyzes how one high-risk dependency is used in the user's project:"
        " static import scan and transitive blast radius from the SBOM dependency tree"
    )


def build_impact_subgraph() -> StateGraph:
    builder = StateGraph(ImpactState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


impact_subgraph = build_impact_subgraph()
