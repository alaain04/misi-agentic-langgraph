from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.remediation.release_research import (
    research_releases_node,
)
from src.main_graph.subgraphs.remediation.select_targets import select_targets_node
from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    group_and_verify_gate,
    pr_and_persist_node,
    remediate_targets_node,
    route_after_group_verify,
)
from src.main_graph.subgraphs.remediation.plan import build_migration_plan_node
from src.main_graph.subgraphs.remediation.state import RemediationState


def build_remediation_subgraph():
    builder = StateGraph(RemediationState)
    builder.add_node("select_targets_node", select_targets_node)
    builder.add_node("research_releases_node", research_releases_node)
    builder.add_node("build_migration_plan_node", build_migration_plan_node)
    builder.add_node("remediate_targets_node", remediate_targets_node)
    builder.add_node("group_and_verify_gate", group_and_verify_gate)
    builder.add_node("pr_and_persist_node", pr_and_persist_node)

    builder.add_edge(START, "select_targets_node")
    builder.add_edge("select_targets_node", "research_releases_node")
    builder.add_edge("research_releases_node", "build_migration_plan_node")
    builder.add_edge("build_migration_plan_node", "remediate_targets_node")
    builder.add_edge("remediate_targets_node", "group_and_verify_gate")
    builder.add_conditional_edges(
        "group_and_verify_gate",
        route_after_group_verify,
        {
            "remediate_targets_node": "remediate_targets_node",
            "pr_and_persist_node": "pr_and_persist_node",
        },
    )
    builder.add_edge("pr_and_persist_node", END)
    return builder.compile()
