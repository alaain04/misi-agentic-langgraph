"""Runtime subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.runtime.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState

GRAPH_NAME = "runtime"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Clones dependency source code, executes its test and audit"
        " scripts inside Docker, and reports pass rate and lint error count"
    )


def build_runtime_subgraph() -> StateGraph:
    builder = StateGraph(RuntimeState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


runtime_subgraph = build_runtime_subgraph()
