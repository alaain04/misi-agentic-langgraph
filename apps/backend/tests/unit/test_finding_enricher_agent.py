from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import FindingEnrichmentDecision, PrepResult, ReportFinding


def _prep(**overrides) -> PrepResult:
    defaults = dict(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={},
        vector_store_id="",
        codegraph_ready=False,
    )
    return PrepResult(**{**defaults, **overrides})


def _finding() -> FindingNote:
    return FindingNote(
        dep_name="left-pad",
        severity="high",
        description="GPL-incompatible",
        evidence=[],
    )


def _draft() -> ReportFinding:
    return ReportFinding(
        dep_name="left-pad",
        severity="high",
        description="GPL-incompatible",
        recommendation="Replace with String.prototype.padStart",
    )


def _finalize(finding: ReportFinding | None = None) -> FindingEnrichmentDecision:
    return FindingEnrichmentDecision(
        tool_calls=[], finding=finding or _draft(), finalize=True, reasoning="done"
    )


def _tool_call_decision() -> FindingEnrichmentDecision:
    return FindingEnrichmentDecision(
        tool_calls=[
            ToolCall(
                tool="web_search",
                args={"package_name": "other-pkg", "query": "q"},
                reason="r",
            )
        ],
        finding=None,
        finalize=False,
        reasoning="looking",
    )


def _ok_verdict():
    from src.main_graph.subgraphs.report.agents.critique import FindingVerdict

    return FindingVerdict(ok=True, feedback="", calibrated_confidence=0.9)


def _reject_verdict(feedback: str = "unsupported"):
    from src.main_graph.subgraphs.report.agents.critique import FindingVerdict

    return FindingVerdict(ok=False, feedback=feedback, calibrated_confidence=0.2)


@pytest.mark.asyncio
async def test_enrich_finding_happy_path_sets_trust_true():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is True
    assert draft.observation == ""
    assert tools_used == []


@pytest.mark.asyncio
async def test_enrich_finding_forces_package_name_on_tool_calls():
    """A subagent cannot fetch evidence for a different package even if the
    LLM passes one — package_name is force-injected server-side."""
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[_tool_call_decision(), _finalize()]
    )
    critic = AsyncMock(return_value=_ok_verdict())

    received: dict = {}

    async def fake_web_search(package_name: str, query: str) -> dict:
        received["package_name"] = package_name
        return {"results": []}

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
        patch.object(finding_enricher_agent, "web_search", fake_web_search),
    ):
        await finding_enricher_agent.enrich_finding(_finding(), _prep(), [])

    # decision passed package_name="other-pkg" but the finding is "left-pad"
    assert received["package_name"] == "left-pad"


@pytest.mark.asyncio
async def test_enrich_finding_self_corrects_then_passes():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(side_effect=[_reject_verdict("add evidence"), _ok_verdict()])

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is True
    assert tools_used == ["verification_feedback"]
    assert critic.await_count == 2


@pytest.mark.asyncio
async def test_enrich_finding_marks_untrusted_when_budget_exhausted():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_reject_verdict("business_impact is not grounded"))

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "_MAX_ITERATIONS", 2),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is False
    assert draft.observation == "business_impact is not grounded"
    assert draft.dep_name == "left-pad"  # kept, not dropped


@pytest.mark.asyncio
async def test_enrich_finding_falls_back_when_llm_never_succeeds():
    """A total LLM outage for this one finding must degrade to a fallback
    finding, not crash the whole enrichment pipeline."""
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM outage")
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "_MAX_ITERATIONS", 2),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.dep_name == "left-pad"
    assert draft.recommendation == "Review manually"
    assert tools_used == []
    critic.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_finding_critic_failure_degrades_to_trusted():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(side_effect=RuntimeError("critic down"))

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is True
    assert draft.observation == ""


@pytest.mark.asyncio
async def test_enrich_finding_excludes_flagged_alternatives_from_prompt():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    seen_system: dict = {}

    async def _ainvoke(messages):
        seen_system["content"] = messages[0]["content"]
        return _finalize()

    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=_ainvoke
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), ["left-pad", "zod", "class-transformer"]
        )

    assert "zod" in seen_system["content"]
    assert "class-transformer" in seen_system["content"]


def test_grounded_impact_analysis_returns_summary_when_present():
    from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
        _grounded_impact_analysis,
    )

    tool_results = [
        ToolResult(
            id="id",
            tool="impact_analysis",
            args={},
            output={
                "available": True,
                "affected_file_count": 1,
                "affected_files": ["scripts/build.js:1"],
                "production_file_count": 0,
                "isolated_to_tests_or_scripts": True,
                "node_count": 3,
                "depth_searched": 3,
                "use_cases_impacted": [],
                "narrative": "Only used in a build script.",
                "source": "codegraph",
            },
            error=None,
            duration_ms=1,
        )
    ]
    summary = _grounded_impact_analysis(tool_results)
    assert summary is not None
    assert summary.affected_file_count == 1
    assert summary.isolated_to_tests_or_scripts is True
    assert summary.narrative == "Only used in a build script."


def test_grounded_impact_analysis_returns_none_when_absent():
    from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
        _grounded_impact_analysis,
    )

    assert _grounded_impact_analysis([]) is None


def _transitive_prep() -> PrepResult:
    return _prep(
        dependency_graph={
            "direct": {"express": "4.18.0"},
            "packages": {
                "express@4.18.0": {
                    "version": "4.18.0",
                    "dependencies": ["left-pad@1.0.0"],
                },
                "left-pad@1.0.0": {"version": "1.0.0", "dependencies": []},
            },
        }
    )


def _direct_prep() -> PrepResult:
    return _prep(
        dependency_graph={
            "direct": {"left-pad": "1.0.0"},
            "packages": {
                "left-pad@1.0.0": {"version": "1.0.0", "dependencies": []},
            },
        }
    )


@pytest.mark.asyncio
async def test_enrich_finding_stamps_transitive_attribution():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, _ = await finding_enricher_agent.enrich_finding(
            _finding(), _transitive_prep(), []
        )

    assert draft.is_direct is False
    assert draft.direct_dependents == ["express"]


@pytest.mark.asyncio
async def test_enrich_finding_stamps_direct_attribution():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, _ = await finding_enricher_agent.enrich_finding(
            _finding(), _direct_prep(), []
        )

    assert draft.is_direct is True
    assert draft.direct_dependents == []


@pytest.mark.asyncio
async def test_enrich_finding_transitive_prompt_names_direct_dependents():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    seen_system: dict = {}

    async def _ainvoke(messages):
        seen_system["content"] = messages[0]["content"]
        return _finalize()

    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=_ainvoke
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        await finding_enricher_agent.enrich_finding(_finding(), _transitive_prep(), [])

    content = seen_system["content"]
    assert "transitive" in content.lower()
    assert "express" in content  # the direct dependent to anchor on
