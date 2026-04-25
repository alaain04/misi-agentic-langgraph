"""Repo subgraph — skeleton with mocked result."""

from langgraph.graph import END, START, StateGraph

from src.graphs.repo.constants import ANALYZE
from src.graphs.repo.nodes import analyze
from src.graphs.repo.state import RepoState


def build_repo_subgraph():
    builder = StateGraph(RepoState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


repo_subgraph = build_repo_subgraph()
