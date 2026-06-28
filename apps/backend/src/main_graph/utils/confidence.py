from __future__ import annotations

from src.models.evidence import Evidence, Severity
from src.models.risk_finding import ContradictionReport

_SEVERITY_ORDER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_SEVERITY_BASE: dict[str, float] = {"critical": 10.0, "high": 7.5, "medium": 5.0, "low": 2.5}


def compute_confidence(
    evidence: list[Evidence],
    contradictions: list[ContradictionReport],
    dep_name: str,
) -> float:
    dep_evs = [e for e in evidence if e.dep_name == dep_name]
    if not dep_evs:
        return 0.0

    base = sum(e.confidence * e.reliability for e in dep_evs) / len(dep_evs)

    dep_ev_ids = {e.id for e in dep_evs}
    unresolved = sum(
        1 for c in contradictions
        if c.resolution == "unresolved" and any(eid in dep_ev_ids for eid in c.evidence_ids)
    )
    penalty = 0.2 * unresolved

    supporting_skills = {e.skill_id for e in dep_evs if e.supports_hypothesis}
    bonus = 0.1 if len(supporting_skills) >= 2 else 0.0

    return max(0.0, min(1.0, base - penalty + bonus))


def compute_severity(evidence: list[Evidence]) -> Severity:
    supporting = [e for e in evidence if e.supports_hypothesis and e.severity]
    if not supporting:
        return "low"
    best = max(supporting, key=lambda e: _SEVERITY_ORDER.get(e.severity or "info", 0))
    return best.severity or "low"


def compute_risk_score(severity: Severity, confidence: float) -> float:
    return round(_SEVERITY_BASE.get(severity, 2.5) * confidence, 1)
