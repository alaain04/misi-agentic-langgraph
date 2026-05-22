from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from src.main_graph.state import MainState
from src.models.evidence import Evidence
from src.models.risk_finding import RiskFinding

logger = logging.getLogger(__name__)

_MAX_REVIEW_ITERATIONS = 2


async def _check_criteria(findings: list[RiskFinding], evidence: list[Evidence]) -> dict:
    failed: list[str] = []

    for f in findings:
        if f.severity in ("critical", "high"):
            if len(f.supporting_evidence) < 2:
                failed.append(f"{f.dep_name}: high-severity finding has fewer than 2 supporting evidence items")
            if f.risk_score > 7 and f.confidence < 0.5:
                failed.append(f"{f.dep_name}: risk_score={f.risk_score} but confidence={f.confidence:.2f} — insufficient evidence")
            if not f.alternatives and not f.recommendation:
                failed.append(f"{f.dep_name}: high-risk dependency has no alternative recommendation")

    for f in findings:
        if f.contradictions and not any(
            c.description[:20] in f.summary for c in f.contradictions
        ):
            failed.append(f"{f.dep_name}: contradictions not addressed in summary")

    return {
        "approved": len(failed) == 0,
        "failed_criteria": failed,
        "feedback": "; ".join(failed) if failed else "",
    }


def _format_findings_for_review(findings: list[RiskFinding]) -> str:
    lines = ["**High-Severity Findings Require Your Review:**\n"]
    for f in findings:
        lines.append(f"**{f.dep_name}** — {f.severity.upper()} (score: {f.risk_score}/10, confidence: {f.confidence:.0%})")
        lines.append(f"  {f.summary}")
        if f.recommendation:
            lines.append(f"  Recommendation: {f.recommendation}")
        if f.alternatives:
            lines.append(f"  Alternatives: {', '.join(f.alternatives)}")
        lines.append("")
    lines.append("Please review these findings. Respond to acknowledge or provide additional context.")
    return "\n".join(lines)


async def finding_reviewer(state: MainState) -> dict:
    findings = state.get("risk_findings") or []
    evidence = state.get("evidence") or []
    iterations = state.get("review_iterations") or 0

    review = await _check_criteria(findings, evidence)

    if not review["approved"] and iterations < _MAX_REVIEW_ITERATIONS:
        logger.info("finding_reviewer: criteria failed, requesting re-correlation. feedback=%s", review["feedback"])
        return {"reviewer_feedback": review["feedback"]}

    high_sev = [f for f in findings if f.severity in ("critical", "high")]
    if high_sev:
        assistant_msg = _format_findings_for_review(high_sev)
        user_input: str = interrupt({
            "risk_findings": [f.__dict__ for f in high_sev],
            "assistant_message": assistant_msg,
        })
        logger.info("finding_reviewer: HITL gate 2 — user acknowledged high-severity findings")
        return {
            "review_approved": True,
            "messages": [AIMessage(content=assistant_msg), HumanMessage(content=user_input)],
        }

    return {"review_approved": True}
