from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.reviewer.nodes import review
from src.main_graph.subgraphs.reviewer.state import ReviewerState


def build_reviewer_subgraph():
    builder = StateGraph(ReviewerState)
    builder.add_node("review", review)
    builder.add_edge(START, "review")
    builder.add_edge("review", END)
    return builder.compile()


reviewer_subgraph = build_reviewer_subgraph()
