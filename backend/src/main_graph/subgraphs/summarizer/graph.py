from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.summarizer.nodes import summarize
from src.main_graph.subgraphs.summarizer.state import SummarizerState


def build_summarizer_subgraph():
    builder = StateGraph(SummarizerState)
    builder.add_node("summarize", summarize)
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", END)
    return builder.compile()


summarizer_subgraph = build_summarizer_subgraph()
