from src.models.evidence import Evidence
from src.models.risk_finding import ContradictionReport
from src.main_graph.utils.confidence import (
    compute_confidence,
    compute_risk_score,
    compute_severity,
)


def _make_evidence(kind, dep, skill, confidence, reliability, supports, severity=None):
    return Evidence(
        kind=kind, dep_name=dep, skill_id=skill, hypothesis_id="h1",
        signal="signal", raw_data={}, source="test",
        reliability=reliability, confidence=confidence,
        supports_hypothesis=supports, severity=severity,
    )


def test_compute_confidence_basic():
    evs = [
        _make_evidence("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True),
        _make_evidence("maintainer_signal", "lodash", "MaintainerTrustSkill", 0.7, 0.8, True),
    ]
    score = compute_confidence(evs, [], "lodash")
    # base = (0.9*0.95 + 0.7*0.8) / 2 = (0.855 + 0.56) / 2 = 0.7075
    # bonus: 2 different skills supporting → +0.1
    assert 0.79 < score <= 0.85


def test_compute_confidence_empty():
    assert compute_confidence([], [], "lodash") == 0.0


def test_compute_confidence_unresolved_contradiction_penalty():
    evs = [_make_evidence("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True)]
    contradictions = [ContradictionReport(
        evidence_ids=[evs[0].id],
        description="test",
        resolution="unresolved",
        adjusted_confidence=0.3,
    )]
    score = compute_confidence(evs, contradictions, "lodash")
    # base ≈ 0.855, penalty = 0.2 → 0.655
    assert score < 0.76


def test_compute_severity_critical_wins():
    evs = [
        _make_evidence("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True, "critical"),
        _make_evidence("ecosystem_signal", "lodash", "EcosystemSkill", 0.5, 0.7, True, "low"),
    ]
    assert compute_severity(evs) == "critical"


def test_compute_severity_no_supporting():
    evs = [_make_evidence("reachability_signal", "lodash", "ReachabilitySkill", 0.8, 0.9, False)]
    assert compute_severity(evs) == "low"


def test_compute_risk_score():
    assert compute_risk_score("critical", 1.0) == 10.0
    assert compute_risk_score("high", 0.5) == 3.8
    assert compute_risk_score("medium", 0.8) == 4.0
    assert compute_risk_score("low", 1.0) == 2.5
