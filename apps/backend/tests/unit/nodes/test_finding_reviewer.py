from src.main_graph.nodes.finding_reviewer import _check_criteria
from src.models.risk_finding import RiskFinding


def _make_finding(dep, score, confidence, severity, evidence_count=2):
    evs = [f"ev{i}" for i in range(evidence_count)]
    return RiskFinding(
        dep_name=dep, risk_score=score, confidence=confidence,
        severity=severity, hypotheses=[], supporting_evidence=evs,
        contradictions=[], missing_evidence=[], summary="test summary",
        recommendation="update package", alternatives=["safer-alt"],
    )


async def test_criteria_pass_when_all_met():
    findings = [_make_finding("lodash", 8.0, 0.8, "high", evidence_count=3)]
    result = await _check_criteria(findings, [])
    assert result["approved"] is True
    assert result["failed_criteria"] == []


async def test_criteria_fail_high_score_low_confidence():
    findings = [_make_finding("lodash", 8.5, 0.3, "high")]
    result = await _check_criteria(findings, [])
    assert result["approved"] is False
    assert any("confidence" in c.lower() for c in result["failed_criteria"])


async def test_criteria_fail_high_sev_no_alternative():
    f = _make_finding("lodash", 8.0, 0.8, "high")
    f.alternatives = []
    f.recommendation = None
    result = await _check_criteria([f], [])
    assert result["approved"] is False
