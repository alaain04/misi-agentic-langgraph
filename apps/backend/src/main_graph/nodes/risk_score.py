"""risk_score node — stub.

Full agentic implementation specified in 2026-05-17-stage3-synthesis-design.md.
"""

import logging

from src.main_graph.state import MainState

_log = logging.getLogger(__name__)


async def risk_score(state: MainState) -> dict:
    """Compute final risk scores per dep — stub assigns 5.0 to all deps."""
    plan_obj = state.get("plan") or {}
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )
    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    scores = [
        {
            "dep_name": dep,
            "score": 5.0,
            "severity": "medium",
            "breakdown": {},
            "rationale": "stub — full analysis pending",
            "impact_weight": None,
        }
        for dep in dep_scope
    ]
    _log.info("risk_score(stub): scored %d deps", len(scores))
    return {"risk_scores": scores}
