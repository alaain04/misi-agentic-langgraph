# tests/unit/models/test_evidence.py
from src.models.evidence import Evidence, EvidenceKind, Severity


def test_evidence_auto_fields():
    ev = Evidence(
        kind="vulnerability",
        dep_name="lodash",
        skill_id="VulnerabilitySkill",
        hypothesis_id="h1",
        signal="CVE-2021-23337 in lodash@4.17.20",
        raw_data={"cve_id": "CVE-2021-23337"},
        source="trivy",
        reliability=0.95,
        confidence=0.9,
        supports_hypothesis=True,
    )
    assert len(ev.id) == 32          # uuid4().hex
    assert "T" in ev.collected_at   # ISO timestamp
    assert ev.contradicts_evidence == []
    assert ev.severity is None
    assert ev.source_url is None


def test_evidence_with_severity():
    ev = Evidence(
        kind="vulnerability",
        dep_name="lodash",
        skill_id="VulnerabilitySkill",
        hypothesis_id="h1",
        signal="critical vuln",
        raw_data={},
        source="trivy",
        reliability=0.95,
        confidence=0.9,
        supports_hypothesis=True,
        severity="critical",
    )
    assert ev.severity == "critical"


def test_evidence_contradicts():
    ev = Evidence(
        kind="reachability_signal",
        dep_name="lodash",
        skill_id="ReachabilitySkill",
        hypothesis_id="h1",
        signal="not imported",
        raw_data={},
        source="ast_scan",
        reliability=0.8,
        confidence=0.82,
        supports_hypothesis=False,
        contradicts_evidence=["ev_abc"],
    )
    assert ev.contradicts_evidence == ["ev_abc"]
