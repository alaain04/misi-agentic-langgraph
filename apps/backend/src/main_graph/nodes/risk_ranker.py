"""risk_ranker — stub agentic node and stage router.

Stub behaviour: selects the first 3 deps in scope as high_risk_deps.
Full implementation follows the stage-3-synthesis spec.
"""

import logging

from src.main_graph.constants import EXECUTION_PLANNER, RISK_SCORE
from src.main_graph.state import MainState

_log = logging.getLogger(__name__)


async def risk_ranker(state: MainState) -> dict:
    """Select high-risk deps and optionally extend execution_stages with Stage 2."""
    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else []
    )
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )

    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    # Stub: select first 3 deps as high-risk
    high_risk_deps = dep_scope[:3]
    _log.info("risk_ranker(stub): high_risk_deps=%s", high_risk_deps)

    risk_rankings = [
        {
            "dep_name": dep,
            "preliminary_score": 5.0,
            "risk_signals": [],
            "rationale": "stub — full analysis pending",
        }
        for dep in dep_scope
    ]

    existing_stages = state.get("execution_stages") or []

    if "impact" in subgraphs and high_risk_deps:
        stage2 = [{"subgraph": "impact", "dep_name": dep} for dep in high_risk_deps]
        new_stages = existing_stages + [stage2]
    else:
        new_stages = list(existing_stages)

    return {
        "execution_stages": new_stages,
        "risk_rankings": risk_rankings,
        "high_risk_deps": high_risk_deps,
        "risk_ranker_done": True,
    }


def risk_ranker_router(state: MainState) -> str:
    """Route after risk_ranker: to Stage 2 dispatch or directly to risk_score."""
    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else []
    )
    if "impact" in subgraphs and state.get("high_risk_deps"):
        return EXECUTION_PLANNER
    return RISK_SCORE
