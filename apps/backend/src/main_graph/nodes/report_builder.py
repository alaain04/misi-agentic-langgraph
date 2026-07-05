"""Deterministic assembly of analysis report from risk findings and contradictions."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.main_graph.state import MainState
from src.models.risk_finding import RiskFinding

logger = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _overall_risk_level(findings: list[RiskFinding]) -> str:
    if not findings:
        return "none"
    return max(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0)).severity


def _aggregate_recommendations(sorted_findings: list[RiskFinding]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for f in sorted_findings:
        if f.recommendation and f.recommendation not in seen:
            seen.add(f.recommendation)
            result.append(f.recommendation)
    return result


def _finding_to_dict(f: RiskFinding) -> dict:
    return {
        "dep_name": f.dep_name,
        "risk_score": f.risk_score,
        "confidence": f.confidence,
        "severity": f.severity,
        "summary": f.summary,
        "recommendation": f.recommendation,
        "alternatives": f.alternatives,
        "supporting_evidence_count": len(f.supporting_evidence),
        "contradictions_count": len(f.contradictions),
        "missing_evidence": f.missing_evidence,
    }


def report_builder(state: MainState) -> dict:
    findings = state.get("risk_findings") or []
    contradictions = state.get("contradictions") or []

    sorted_findings = sorted(findings, key=lambda f: f.risk_score, reverse=True)

    report = {
        "concern": state.get("concern", ""),
        "generated_at": datetime.now(UTC).isoformat(),
        "overall_risk_level": _overall_risk_level(findings),
        "summary": {
            "total_deps": len(findings),
            "critical": sum(1 for f in findings if f.severity == "critical"),
            "high": sum(1 for f in findings if f.severity == "high"),
            "medium": sum(1 for f in findings if f.severity == "medium"),
            "low": sum(1 for f in findings if f.severity == "low"),
        },
        "findings": [_finding_to_dict(f) for f in sorted_findings],
        "recommendations": _aggregate_recommendations(sorted_findings),
        "contradictions": [
            {"description": c.description, "resolution": c.resolution}
            for c in contradictions
        ],
    }

    logger.info(
        "report_builder: overall_risk=%s findings=%d recommendations=%d",
        report["overall_risk_level"], len(findings), len(report["recommendations"]),
    )
    return {"analysis_report": report}
