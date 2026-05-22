from src.main_graph.nodes.evidence_correlator import (
    _detect_contradictions,
    _group_by_dep,
)
from src.models.evidence import Evidence


def _ev(kind, dep, skill, confidence, reliability, supports, severity=None):
    return Evidence(
        kind=kind, dep_name=dep, skill_id=skill, hypothesis_id="h1",
        signal="s", raw_data={}, source="test",
        reliability=reliability, confidence=confidence,
        supports_hypothesis=supports, severity=severity,
    )


def test_group_by_dep():
    evs = [
        _ev("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True),
        _ev("license_signal", "lodash", "LicenseSkill", 0.7, 0.8, False),
        _ev("vulnerability", "express", "VulnerabilitySkill", 0.5, 0.9, True),
    ]
    grouped = _group_by_dep(evs)
    assert len(grouped["lodash"]) == 2
    assert len(grouped["express"]) == 1


def test_detect_contradictions_vuln_unreachable():
    vuln_ev = _ev("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True, "high")
    reach_ev = _ev("reachability_signal", "lodash", "ReachabilitySkill", 0.82, 0.8, True)
    # ReachabilitySkill supports_hypothesis=True means dep is UNREACHABLE

    contradictions = _detect_contradictions([vuln_ev, reach_ev])

    assert len(contradictions) == 1
    c = contradictions[0]
    assert "lodash" in c.description
    assert c.resolution == "effective_risk_reduced"
    assert c.adjusted_confidence < 0.5


def test_detect_contradictions_no_contradiction():
    vuln_ev = _ev("vulnerability", "lodash", "VulnerabilitySkill", 0.9, 0.95, True, "high")
    contradictions = _detect_contradictions([vuln_ev])
    assert len(contradictions) == 0
