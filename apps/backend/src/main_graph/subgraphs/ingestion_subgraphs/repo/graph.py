"""Repo subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.repo.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState

GRAPH_NAME = "repo"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Fetches GitHub signals for one dependency via the workers service:"
        " issues, releases, and security advisories, then curates them with LLM agents"
    )


def build_repo_subgraph() -> StateGraph:
    builder = StateGraph(RepoState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


repo_subgraph = build_repo_subgraph()
