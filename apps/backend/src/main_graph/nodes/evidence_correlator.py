from __future__ import annotations

import logging

from src.main_graph.state import MainState
from src.main_graph.utils.confidence import compute_confidence, compute_risk_score, compute_severity
from src.models.evidence import Evidence
from src.models.hypothesis import Hypothesis
from src.models.risk_finding import ContradictionReport, RiskFinding
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_SYNTHESIS_SYSTEM = """\
You are a dependency risk synthesis engine. Given structured evidence for a dependency, produce a concise risk assessment.

The risk_score and confidence are already computed — do NOT change them.
Output ONLY a JSON object:
{
  "summary": "<2-3 sentence assessment of the risk>",
  "recommendation": "<action to take, or null>",
  "alternatives": ["<maintained alternative package>"]
}
"""


def _group_by_dep(evidence: list[Evidence]) -> dict[str, list[Evidence]]:
    result: dict[str, list[Evidence]] = {}
    for e in evidence:
        result.setdefault(e.dep_name, []).append(e)
    return result


def _detect_contradictions(evidence: list[Evidence]) -> list[ContradictionReport]:
    contradictions = []
    by_dep = _group_by_dep(evidence)

    for dep_name, evs in by_dep.items():
        vuln_evs = [
            e for e in evs
            if e.kind == "vulnerability" and e.supports_hypothesis
            and e.severity in ("critical", "high")
        ]
        # ReachabilitySkill with supports_hypothesis=True means dep is NOT reachable
        unreachable_evs = [
            e for e in evs
            if e.kind == "reachability_signal" and e.supports_hypothesis
        ]

        if vuln_evs and unreachable_evs:
            all_ids = [e.id for e in vuln_evs + unreachable_evs]
            max_conf = max(e.confidence for e in vuln_evs)
            contradictions.append(ContradictionReport(
                evidence_ids=all_ids,
                description=f"{dep_name}: high-severity vulnerability but dependency appears unreachable",
                resolution="effective_risk_reduced",
                adjusted_confidence=max_conf * 0.35,
            ))

    return contradictions


async def _synthesize_finding(
    dep_name: str,
    evs: list[Evidence],
    hypotheses: list[Hypothesis],
    risk_score: float,
    confidence: float,
    severity: str,
    contradictions: list[ContradictionReport],
    concern: str,
) -> RiskFinding:
    dep_hyps = [h for h in hypotheses if h.dep_name == dep_name]
    evidence_summary = "\n".join(f"- [{e.skill_id}] {e.signal}" for e in evs[:10])
    contradiction_summary = "\n".join(f"- {c.description}" for c in contradictions) or "None"

    response = await _llm.ainvoke([
        {"role": "system", "content": _SYNTHESIS_SYSTEM},
        {"role": "user", "content": (
            f"Dependency: {dep_name}\n"
            f"Concern: {concern}\n"
            f"Risk score: {risk_score}/10 (confidence: {confidence:.2f})\n"
            f"Severity: {severity}\n"
            f"Evidence:\n{evidence_summary}\n"
            f"Contradictions:\n{contradiction_summary}"
        )},
    ])

    parsed = parse_llm_json(response.content or "{}")

    return RiskFinding(
        dep_name=dep_name,
        risk_score=risk_score,
        confidence=confidence,
        severity=severity,
        hypotheses=dep_hyps,
        supporting_evidence=[e.id for e in evs if e.supports_hypothesis],
        contradictions=contradictions,
        missing_evidence=[],
        summary=parsed.get("summary", ""),
        recommendation=parsed.get("recommendation"),
        alternatives=parsed.get("alternatives", []),
    )


async def evidence_correlator(state: MainState) -> dict:
    evidence = state.get("evidence") or []
    plan = state.get("investigation_plan")
    concern = state.get("concern", "")
    hypotheses = plan.hypotheses if plan else []

    by_dep = _group_by_dep(evidence)
    contradictions = _detect_contradictions(evidence)

    findings = []
    for dep_name, evs in by_dep.items():
        dep_ev_ids = {e.id for e in evs}
        dep_contradictions = [
            c for c in contradictions
            if any(eid in dep_ev_ids for eid in c.evidence_ids)
        ]
        confidence = compute_confidence(evidence, dep_contradictions, dep_name)
        severity = compute_severity(evs)
        risk_score = compute_risk_score(severity, confidence)

        finding = await _synthesize_finding(
            dep_name, evs, hypotheses, risk_score, confidence, severity,
            dep_contradictions, concern,
        )
        findings.append(finding)

    logger.info("evidence_correlator: %d findings, %d contradictions", len(findings), len(contradictions))
    return {
        "risk_findings": findings,
        "contradictions": contradictions,
        "reviewer_feedback": None,
        "review_iterations": (state.get("review_iterations") or 0) + 1,
    }
