from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="vulnerabilities", hypothesis="express has a known CVE",
        packages_to_focus=["express"], agent_type="vulnerability_agent",
    )


def test_format_findings_marks_missing_evidence():
    from src.main_graph.subgraphs.analysis.agents.critique import _format_findings

    f = FindingNote(dep_name="express", severity="high", description="CVE-123", evidence=[])
    rendered = _format_findings([f])
    assert "express" in rendered
    assert "no evidence attached" in rendered


def test_format_findings_includes_snippets():
    from src.main_graph.subgraphs.analysis.agents.critique import _format_findings

    f = FindingNote(
        dep_name="express", severity="high", description="CVE-123",
        evidence=[EvidenceRef(tool="npm_audit", url=None, log_snippet="advisory 1234 high")],
    )
    rendered = _format_findings([f])
    assert "advisory 1234 high" in rendered
    assert "npm_audit" in rendered


@pytest.mark.asyncio
async def test_critique_findings_returns_verdict():
    from src.main_graph.subgraphs.analysis.agents.critique import FindingsVerdict, critique_findings

    verdict = FindingsVerdict(ok=False, feedback="finding 1 lacks evidence", calibrated_confidence=0.2)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=verdict)

    f = FindingNote(dep_name="express", severity="high", description="CVE-123", evidence=[])
    with patch("src.main_graph.subgraphs.analysis.agents.critique._llm", mock_llm):
        result = await critique_findings(_dispatch(), [f])

    assert result.ok is False
    assert result.calibrated_confidence == 0.2
    mock_llm.with_structured_output.assert_called_once()
