from src.models.risk_finding import ContradictionReport, RiskFinding


def test_contradiction_report():
    c = ContradictionReport(
        evidence_ids=["ev1", "ev2"],
        description="high CVE but dep is unreachable",
        resolution="effective_risk_reduced",
        adjusted_confidence=0.35,
    )
    assert c.adjusted_confidence == 0.35


def test_risk_finding_defaults():
    from src.models.hypothesis import Hypothesis
    h = Hypothesis(id="h1", dep_name="lodash", statement="s", risk_theme="vulnerability", rationale="r", skills=[])
    f = RiskFinding(
        dep_name="lodash",
        risk_score=7.2,
        confidence=0.8,
        severity="high",
        hypotheses=[h],
        supporting_evidence=["ev1"],
        contradictions=[],
        missing_evidence=[],
        summary="lodash has a high-severity CVE with strong evidence",
    )
    assert f.recommendation is None
    assert f.alternatives == []
