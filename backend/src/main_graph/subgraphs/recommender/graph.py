from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.recommender.nodes import recommend
from src.main_graph.subgraphs.recommender.state import RecommenderState


def build_recommender_subgraph():
    builder = StateGraph(RecommenderState)
    builder.add_node("recommend", recommend)
    builder.add_edge(START, "recommend")
    builder.add_edge("recommend", END)
    return builder.compile()


recommender_subgraph = build_recommender_subgraph()
