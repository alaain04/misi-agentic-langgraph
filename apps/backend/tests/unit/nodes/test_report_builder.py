from src.main_graph.nodes.report_builder import report_builder
from src.models.risk_finding import RiskFinding, ContradictionReport


def _make_finding(dep, score, severity):
    return RiskFinding(
        dep_name=dep, risk_score=score, confidence=0.8,
        severity=severity, hypotheses=[], supporting_evidence=[],
        contradictions=[], missing_evidence=[],
        summary=f"{dep} summary", recommendation="update", alternatives=["alt"],
    )


def test_report_builder_structure():
    state = {
        "concern": "security audit",
        "risk_findings": [
            _make_finding("lodash", 8.5, "high"),
            _make_finding("express", 3.0, "low"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    report = result["analysis_report"]

    assert report["concern"] == "security audit"
    assert report["summary"]["total_deps"] == 2
    assert report["summary"]["high"] == 1
    assert report["summary"]["low"] == 1
    assert report["findings"][0]["dep_name"] == "lodash"  # sorted by risk_score desc


def test_report_builder_empty_findings():
    state = {"concern": "test", "risk_findings": [], "contradictions": []}
    result = report_builder(state)
    assert result["analysis_report"]["summary"]["total_deps"] == 0


def test_report_builder_with_contradictions():
    state = {
        "concern": "audit",
        "risk_findings": [_make_finding("lodash", 7.0, "high")],
        "contradictions": [
            ContradictionReport(
                evidence_ids=["e1", "e2"],
                description="Vulnerability vs unreachable code",
                resolution="effective_risk_reduced",
                adjusted_confidence=0.6,
            )
        ],
    }
    result = report_builder(state)
    report = result["analysis_report"]

    assert len(report["contradictions"]) == 1
    assert report["contradictions"][0]["description"] == "Vulnerability vs unreachable code"
    assert report["contradictions"][0]["resolution"] == "effective_risk_reduced"


def test_report_builder_severity_counts():
    state = {
        "concern": "security",
        "risk_findings": [
            _make_finding("dep1", 9.5, "critical"),
            _make_finding("dep2", 8.0, "critical"),
            _make_finding("dep3", 7.0, "high"),
            _make_finding("dep4", 5.0, "medium"),
            _make_finding("dep5", 2.0, "low"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    summary = result["analysis_report"]["summary"]

    assert summary["critical"] == 2
    assert summary["high"] == 1
    assert summary["medium"] == 1
    assert summary["low"] == 1
    assert summary["total_deps"] == 5


def test_report_builder_findings_sorted_by_score():
    state = {
        "concern": "audit",
        "risk_findings": [
            _make_finding("z_dep", 3.0, "low"),
            _make_finding("a_dep", 9.0, "high"),
            _make_finding("m_dep", 5.0, "medium"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    findings = result["analysis_report"]["findings"]

    assert findings[0]["dep_name"] == "a_dep"
    assert findings[1]["dep_name"] == "m_dep"
    assert findings[2]["dep_name"] == "z_dep"


def test_report_builder_overall_risk_level_picks_max_severity():
    state = {
        "concern": "security",
        "risk_findings": [
            _make_finding("lodash", 8.5, "high"),
            _make_finding("express", 3.0, "low"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    assert result["analysis_report"]["overall_risk_level"] == "high"


def test_report_builder_overall_risk_level_none_when_no_findings():
    state = {"concern": "test", "risk_findings": [], "contradictions": []}
    result = report_builder(state)
    assert result["analysis_report"]["overall_risk_level"] == "none"


def test_report_builder_recommendations_deduplicated():
    # both findings have recommendation="update" — should appear once
    state = {
        "concern": "security",
        "risk_findings": [
            _make_finding("lodash", 8.5, "high"),
            _make_finding("express", 3.0, "low"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    assert result["analysis_report"]["recommendations"] == ["update"]


def test_report_builder_recommendations_empty_when_all_null():
    from src.models.risk_finding import RiskFinding
    finding = RiskFinding(
        dep_name="dep", risk_score=1.0, confidence=0.5, severity="low",
        hypotheses=[], supporting_evidence=[], contradictions=[], missing_evidence=[],
        summary="s", recommendation=None, alternatives=[],
    )
    state = {"concern": "t", "risk_findings": [finding], "contradictions": []}
    result = report_builder(state)
    assert result["analysis_report"]["recommendations"] == []


def test_report_builder_recommendations_ordered_by_risk_score():
    from src.models.risk_finding import RiskFinding

    def _make_rec(dep, score, severity, rec):
        return RiskFinding(
            dep_name=dep, risk_score=score, confidence=0.8, severity=severity,
            hypotheses=[], supporting_evidence=[], contradictions=[], missing_evidence=[],
            summary=f"{dep} summary", recommendation=rec, alternatives=[],
        )

    state = {
        "concern": "security",
        "risk_findings": [
            _make_rec("z_dep", 2.0, "low", "pin z_dep"),
            _make_rec("a_dep", 9.0, "high", "update a_dep immediately"),
            _make_rec("m_dep", 5.0, "medium", "monitor m_dep"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    recs = result["analysis_report"]["recommendations"]
    assert recs[0] == "update a_dep immediately"
    assert recs[1] == "monitor m_dep"
    assert recs[2] == "pin z_dep"
