"""Repo subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.repo.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState

GRAPH_NAME = "repo"
DEPENDS_ON: list[str] = ["registry"]


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Inspects source repository health including"
        " maintenance status, open issues, activity, and license compliance"
    )


def build_repo_subgraph() -> StateGraph:
    builder = StateGraph(RepoState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


repo_subgraph = build_repo_subgraph()
