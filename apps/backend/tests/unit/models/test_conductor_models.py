import pytest
from src.models.conductor import ConductorDecision, FindingNote, ToolCall, ToolResult


def test_tool_call_requires_tool_and_args():
    tc = ToolCall(tool="npm_audit", args={"repo_path": "/tmp/repo"}, reason="check vulns")
    assert tc.tool == "npm_audit"
    assert tc.args == {"repo_path": "/tmp/repo"}


def test_finding_note_severity_values():
    for sev in ("critical", "high", "medium", "low", "info"):
        fn = FindingNote(dep_name="lodash", severity=sev, description="desc", evidence_refs=["tr-1"])
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
