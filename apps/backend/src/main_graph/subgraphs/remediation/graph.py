from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.remediation.nodes.remediate import remediate
from src.main_graph.subgraphs.remediation.state import RemediationState


def build_remediation_subgraph():
    builder = StateGraph(RemediationState)
    builder.add_node("remediate", remediate)
    builder.add_edge(START, "remediate")
    builder.add_edge("remediate", END)
    return builder.compile()
