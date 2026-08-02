from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.remediation.classify import classify_targets_node
from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    group_and_verify_gate,
    pr_and_persist_node,
    root_deepagent_node,
    route_after_group_verify,
)
from src.main_graph.subgraphs.remediation.state import RemediationState


def build_remediation_subgraph():
    builder = StateGraph(RemediationState)
    builder.add_node("classify_targets_node", classify_targets_node)
    builder.add_node("root_deepagent_node", root_deepagent_node)
    builder.add_node("group_and_verify_gate", group_and_verify_gate)
    builder.add_node("pr_and_persist_node", pr_and_persist_node)
    builder.add_edge(START, "classify_targets_node")
    builder.add_edge("classify_targets_node", "root_deepagent_node")
    builder.add_edge("root_deepagent_node", "group_and_verify_gate")
    builder.add_conditional_edges(
        "group_and_verify_gate",
        route_after_group_verify,
        {
            "root_deepagent_node": "root_deepagent_node",
            "pr_and_persist_node": "pr_and_persist_node",
        },
    )
    builder.add_edge("pr_and_persist_node", END)
    return builder.compile()
