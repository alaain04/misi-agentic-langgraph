from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.conductor import FindingNote, ToolResult
from src.models.results import ReportFinding


def _original() -> FindingNote:
    return FindingNote(
        dep_name="left-pad", severity="high", description="GPL-incompatible", evidence=[]
    )


def _draft() -> ReportFinding:
    return ReportFinding(
        dep_name="left-pad",
        severity="high",
        description="GPL-incompatible",
        recommendation="Replace with String.prototype.padStart",
    )


def _tool_result(tool: str, output: dict, error: str | None = None) -> ToolResult:
    return ToolResult(
        id="id", tool=tool, args={}, output=output, error=error, duration_ms=1
    )


def test_format_tool_results_renders_errors_and_output():
    from src.main_graph.subgraphs.report.agents.critique import _format_tool_results

    results = [
        _tool_result("web_search", {"results": ["x"]}),
        _tool_result("blast_radius", {}, error="timed out"),
    ]
    rendered = _format_tool_results(results)
    assert "[web_search]" in rendered
    assert "ERROR: timed out" in rendered


def test_format_tool_results_handles_empty():
    from src.main_graph.subgraphs.report.agents.critique import _format_tool_results

    assert _format_tool_results([]) == "(no tool results)"


@pytest.mark.asyncio
async def test_critique_report_finding_returns_verdict():
    from src.main_graph.subgraphs.report.agents.critique import (
        FindingVerdict,
        critique_report_finding,
    )

    verdict = FindingVerdict(
        ok=False, feedback="business_impact is not grounded", calibrated_confidence=0.2
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=verdict
    )

    with patch("src.main_graph.subgraphs.report.agents.critique._llm", mock_llm):
        result = await critique_report_finding(_original(), _draft(), [])

    assert result.ok is False
    assert result.calibrated_confidence == 0.2
    mock_llm.with_structured_output.assert_called_once()
