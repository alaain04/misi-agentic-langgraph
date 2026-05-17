"""Cross-analyzer node — assembles the unified structured report from Stage 3 artifacts."""

import logging
from datetime import UTC, datetime

from src.main_graph.subgraphs.cross_analyzer.state import CrossAnalyzerState

logger = logging.getLogger(__name__)


async def analyze(state: CrossAnalyzerState) -> dict:
    concern = state.get("concern", "")
    feedback = state.get("reviewer_feedback")
    iteration = state.get("review_iterations", 0)

    if feedback:
        logger.info(
            "cross_analyzer: rebuilding report on iteration %d — feedback: %s",
            iteration,
            feedback,
        )

    risk_scores = state.get("risk_scores") or []
    recommendations = state.get("recommendations") or []
    risk_rankings = state.get("risk_rankings") or []

    by_dep: dict[str, dict] = {}
    for entry in risk_scores:
        dep = entry.get("dep_name", "")
        by_dep.setdefault(dep, {})["risk_score"] = entry

    for entry in recommendations:
        dep = entry.get("dep_name", "")
        by_dep.setdefault(dep, {})["recommendation"] = entry

    for entry in risk_rankings:
        dep = entry.get("dep_name", "")
        by_dep.setdefault(dep, {})["ranking"] = entry

    report: dict = {
        "concern": concern,
        "generated_at": datetime.now(UTC).isoformat(),
        "iteration": iteration + 1,
        "dependencies": by_dep,
        "reviewer_notes": feedback or None,
    }

    logger.info("cross_analyzer: report built, deps=%d", len(by_dep))
    return {
        "analysis_report": report,
        "review_iterations": iteration + 1,
        "reviewer_feedback": None,
    }
