import pytest
from src.models.conductor import ConductorDecision, EvidenceRef, FindingNote, ToolCall, ToolResult


def test_tool_call_requires_tool_and_args():
    tc = ToolCall(tool="npm_audit", args={"repo_path": "/tmp/repo"}, reason="check vulns")
    assert tc.tool == "npm_audit"
    assert tc.args == {"repo_path": "/tmp/repo"}


def test_finding_note_severity_values():
    for sev in ("critical", "high", "medium", "low", "info"):
        fn = FindingNote(dep_name="lodash", severity=sev, description="desc", evidence=[])
        assert fn.severity == sev


def test_tool_result_defaults():
    tr = ToolResult(id="abc", tool="npm_list", args={}, output={"deps": []}, error=None, duration_ms=42)
    assert tr.error is None
    assert tr.duration_ms == 42


def test_conductor_decision_defaults():
    d = ConductorDecision(
        tool_calls=[],
        findings=[],
        ask_user=None,
        checkpoint_message=None,
        finalize=False,
        reasoning="thinking",
    )
    assert not d.finalize
    assert d.ask_user is None


def test_evidence_ref_fields():
    ev = EvidenceRef(tool="npm_audit", url="https://example.com/advisory", log_snippet="critical vuln found")
    assert ev.tool == "npm_audit"
    assert ev.url == "https://example.com/advisory"
    assert ev.log_snippet == "critical vuln found"


def test_evidence_ref_url_nullable():
    ev = EvidenceRef(tool="npm_list", url=None, log_snippet="lodash 4.17.21")
    assert ev.url is None


def test_finding_note_uses_evidence_not_evidence_refs():
    ev = EvidenceRef(tool="npm_audit", url=None, log_snippet="vuln")
    finding = FindingNote(dep_name="lodash", severity="high", description="outdated", evidence=[ev])
    assert len(finding.evidence) == 1
    assert finding.evidence[0].tool == "npm_audit"
