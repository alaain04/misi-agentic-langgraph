"""Deterministic assembly of analysis report from risk findings and contradictions."""
from __future__ import annotations

from datetime import UTC, datetime

from src.main_graph.state import MainState
from src.models.risk_finding import RiskFinding


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
        "summary": {
            "total_deps": len(findings),
            "critical": sum(1 for f in findings if f.severity == "critical"),
            "high": sum(1 for f in findings if f.severity == "high"),
            "medium": sum(1 for f in findings if f.severity == "medium"),
            "low": sum(1 for f in findings if f.severity == "low"),
        },
        "findings": [_finding_to_dict(f) for f in sorted_findings],
        "contradictions": [
            {"description": c.description, "resolution": c.resolution}
            for c in contradictions
        ],
    }

    return {"analysis_report": report}
