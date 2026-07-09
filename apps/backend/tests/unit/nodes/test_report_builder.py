from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.report_builder import report_builder
from src.models.conductor import EvidenceRef, FindingNote


def _make_state(findings=None, concern="security"):
    return {
        "repo_url": "https://github.com/test/repo",
        "concern": concern,
        "job_id": "j1",
        "autopilot": False,
        "tool_results": [],
        "findings": findings or [],
        "conductor_iteration": 3,
        "messages": [],
    }


@pytest.mark.asyncio
async def test_report_builder_returns_none_risk_when_no_findings():
    result = await report_builder(_make_state(findings=[]))
    assert result["analysis_report"]["overall_risk_level"] == "none"


@pytest.mark.asyncio
async def test_report_builder_calls_llm_with_findings():
    findings = [
        FindingNote(dep_name="lodash", severity="high", description="vuln", evidence=[
            EvidenceRef(tool="npm_audit", url="https://example.com/cve-1", log_snippet="critical issue in lodash")
        ]),
        FindingNote(dep_name="express", severity="medium", description="outdated", evidence=[]),
    ]
    mock_response = MagicMock()
    mock_response.content = '{"executive_summary": "High risk.", "overall_risk_level": "high", "findings": [], "recommendations": ["upgrade lodash"]}'
    with patch("src.main_graph.nodes.report_builder._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        result = await report_builder(_make_state(findings=findings))
    assert result["analysis_report"]["overall_risk_level"] == "high"
    assert "generated_at" in result["analysis_report"]
