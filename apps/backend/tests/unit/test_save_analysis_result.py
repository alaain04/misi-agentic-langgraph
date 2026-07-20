from __future__ import annotations

from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import dedup_findings
from src.models.conductor import EvidenceRef, FindingNote


def _finding(dep_name: str, severity: str, description: str) -> FindingNote:
    return FindingNote(
        dep_name=dep_name,
        severity=severity,
        description=description,
        evidence=[EvidenceRef(tool="npm_audit", url=None, log_snippet="x")],
    )


def test_dedup_collapses_identical_findings():
    f = _finding("electron", "critical", "CVE-1; affected <=39.8.4")
    result = dedup_findings([f, f])
    assert len(result) == 1
    assert result[0].dep_name == "electron"


def test_dedup_preserves_distinct_findings_on_same_dep():
    # same dep, different description = two distinct issues, both kept
    a = _finding("electron", "critical", "vulnerability advisory")
    b = _finding("electron", "medium", "install script risk")
    result = dedup_findings([a, b])
    assert len(result) == 2


def test_dedup_is_order_stable_keeps_first():
    a = _finding("minimatch", "high", "ReDoS 9.0.0-9.0.6")
    b = _finding("xo", "high", "vulnerable transitive")
    dup_a = _finding("minimatch", "high", "ReDoS 9.0.0-9.0.6")
    result = dedup_findings([a, b, dup_a])
    assert [f.dep_name for f in result] == ["minimatch", "xo"]


def test_dedup_empty_list():
    assert dedup_findings([]) == []
