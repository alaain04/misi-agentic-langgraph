"""Recommendation subgraph — skeleton with mocked result."""

from langgraph.graph import END, START, StateGraph

from src.graphs.recommendation.constants import ANALYZE
from src.graphs.recommendation.nodes import analyze
from src.graphs.recommendation.state import RecommendationState


def build_recommendation_subgraph():
    builder = StateGraph(RecommendationState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


recommendation_subgraph = build_recommendation_subgraph()
