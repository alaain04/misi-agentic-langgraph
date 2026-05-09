"""Report reviewer subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.report_reviewer.constants import REVIEW
from src.main_graph.subgraphs.report_reviewer.nodes import review
from src.main_graph.subgraphs.report_reviewer.state import ReportReviewerState


def build_report_reviewer_subgraph():
    builder = StateGraph(ReportReviewerState)
    builder.add_node(REVIEW, review)
    builder.add_edge(START, REVIEW)
    builder.add_edge(REVIEW, END)
    return builder.compile()


report_reviewer_subgraph = build_report_reviewer_subgraph()
