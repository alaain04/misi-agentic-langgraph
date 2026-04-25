"""Risk score subgraph — skeleton with mocked result."""

from langgraph.graph import END, START, StateGraph

from src.graphs.risk_score.constants import ANALYZE
from src.graphs.risk_score.nodes import analyze
from src.graphs.risk_score.state import RiskScoreState


def build_risk_score_subgraph():
    builder = StateGraph(RiskScoreState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


risk_score_subgraph = build_risk_score_subgraph()
