from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.remediation.state import RemediationState


async def _remediate_placeholder(state: RemediationState) -> dict:
    """Temporary placeholder. The real deepagent-based implementation lands
    in Task 8/9 of the remediation deepagent tier-ladder plan (see
    docs/superpowers/plans/), which replaces this node entirely. Kept
    import-safe in the interim so every other task in that plan can run
    its own test suite without tripping over the deleted
    orchestrator.py/nodes/remediate.py this placeholder replaces ahead of
    schedule."""
    raise NotImplementedError(
        "remediation subgraph placeholder - real implementation lands later in "
        "docs/superpowers/plans/2026-07-27-remediation-deepagent-tier-ladder.md"
    )


def build_remediation_subgraph():
    builder = StateGraph(RemediationState)
    builder.add_node("remediate_placeholder", _remediate_placeholder)
    builder.add_edge(START, "remediate_placeholder")
    builder.add_edge("remediate_placeholder", END)
    return builder.compile()
