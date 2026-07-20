from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.tools import tool

from src.models.conductor import FindingNote, ToolCall
from src.models.results import BlastRadiusSummary, ImpactAnalysisDecision, PrepResult


def _prep(**overrides) -> PrepResult:
    defaults = dict(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={},
        discovery_summary="s",
        vector_store_id="vs-1",
        codegraph_ready=True,
    )
    return PrepResult(**{**defaults, **overrides})


def _finding() -> FindingNote:
    return FindingNote(
        dep_name="left-pad",
        severity="high",
        description="GPL-incompatible",
        evidence=[],
    )


def _finalize(narrative: str = "", use_cases: list[str] | None = None):
    return ImpactAnalysisDecision(
        tool_calls=[],
        narrative=narrative,
        use_cases_impacted=use_cases or [],
        finalize=True,
        reasoning="done",
    )


def _blast_radius_call(package_name: str = "left-pad") -> ImpactAnalysisDecision:
    return ImpactAnalysisDecision(
        tool_calls=[
            ToolCall(
                tool="blast_radius",
                args={"package_name": package_name},
                reason="check graph",
            )
        ],
        narrative="",
        use_cases_impacted=[],
        finalize=False,
        reasoning="checking",
    )


def _find_usage_sites_call() -> ImpactAnalysisDecision:
    return ImpactAnalysisDecision(
        tool_calls=[ToolCall(tool="find_usage_sites", args={}, reason="fallback")],
        narrative="",
        use_cases_impacted=[],
        finalize=False,
        reasoning="falling back",
    )


def _fake_blast_radius_factory(available: bool, **output_overrides):
    # make_blast_radius_tool is a SYNC factory (it returns the @tool object
    # directly; only the tool's own body is async) — the fake must match
    # that signature or _build_internal_tool_map's unawaited call returns a
    # coroutine instead of a tool.
    def _make(repo_path, container, image):
        @tool
        async def blast_radius(package_name: str, depth: int = 3) -> dict:
            """Fake blast_radius tool for testing."""
            if not available:
                return {"package_name": package_name, "available": False}
            return {
                "package_name": package_name,
                "available": True,
                "affected_file_count": 2,
                "affected_files": ["src/checkout.ts:10"],
                "production_file_count": 2,
                "isolated_to_tests_or_scripts": False,
                "node_count": 5,
                "depth_searched": 3,
                **output_overrides,
            }

        return blast_radius

    return _make


@pytest.mark.asyncio
async def test_analyze_impact_grounds_from_blast_radius_when_available():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[_blast_radius_call(), _finalize("Used in checkout.", ["checkout"])]
    )

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(
            impact_analysis_agent,
            "make_blast_radius_tool",
            _fake_blast_radius_factory(available=True),
        ),
    ):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(), container=None
        )

    assert summary.available is True
    assert summary.source == "codegraph"
    assert summary.affected_file_count == 2
    assert summary.affected_files == ["src/checkout.ts:10"]
    assert summary.narrative == "Used in checkout."
    assert summary.use_cases_impacted == ["checkout"]


@pytest.mark.asyncio
async def test_analyze_impact_falls_back_to_find_usage_sites_when_unavailable():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[
            _blast_radius_call(),
            _find_usage_sites_call(),
            _finalize("Used in auth.", ["auth"]),
        ]
    )

    async def fake_find_usage_sites(package_name: str) -> list[dict]:
        return [{"file": "src/auth.ts", "snippet": "import left-pad"}]

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(
            impact_analysis_agent,
            "make_blast_radius_tool",
            _fake_blast_radius_factory(available=False),
        ),
        patch.object(
            impact_analysis_agent,
            "_make_find_usage_sites_tool",
            lambda vector_store_id: fake_find_usage_sites,
        ),
    ):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(), container=None
        )

    assert summary.available is True
    assert summary.source == "semantic_search"
    assert summary.affected_files == ["src/auth.ts"]
    assert summary.affected_file_count == 1
    assert summary.narrative == "Used in auth."


@pytest.mark.asyncio
async def test_analyze_impact_returns_unavailable_when_no_tool_data():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )

    with patch.object(impact_analysis_agent, "_llm", mock_llm):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(vector_store_id=""), container=None
        )

    assert summary.available is False
    assert summary.source == "unavailable"
    assert summary.affected_files == []


@pytest.mark.asyncio
async def test_analyze_impact_forces_package_name_on_internal_tool_calls():
    """The nested loop's own LLM cannot query a different package's evidence,
    exactly like the outer finding_enricher loop can't — mirrors the same
    force-injection guarantee one level down."""
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[_blast_radius_call(package_name="other-pkg"), _finalize()]
    )

    received: dict = {}

    def _make(repo_path, container, image):  # sync, matches make_blast_radius_tool
        @tool
        async def blast_radius(package_name: str, depth: int = 3) -> dict:
            """Fake blast_radius tool for testing."""
            received["package_name"] = package_name
            return {"package_name": package_name, "available": True}

        return blast_radius

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(impact_analysis_agent, "make_blast_radius_tool", _make),
    ):
        await impact_analysis_agent.analyze_impact(_finding(), _prep(), container=None)

    assert received["package_name"] == "left-pad"


@pytest.mark.asyncio
async def test_analyze_impact_degrades_gracefully_on_malformed_tool_output():
    """A malformed field in the real blast_radius tool's output (e.g. from
    parsing external codegraph CLI JSON) must not let a Pydantic
    ValidationError escape analyze_impact's "never raises" contract."""
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[_blast_radius_call(), _finalize("Used in checkout.", ["checkout"])]
    )

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(
            impact_analysis_agent,
            "make_blast_radius_tool",
            _fake_blast_radius_factory(
                available=True, affected_file_count="not-a-number"
            ),
        ),
    ):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(), container=None
        )

    assert summary == BlastRadiusSummary(available=False, source="unavailable")


@pytest.mark.asyncio
async def test_analyze_impact_forces_depth_on_internal_tool_calls():
    """The nested loop's own LLM cannot silently ignore the caller's depth
    control, mirroring the package_name force-injection guarantee."""
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[
            ImpactAnalysisDecision(
                tool_calls=[
                    ToolCall(
                        tool="blast_radius",
                        args={"package_name": "left-pad", "depth": 1},
                        reason="check graph",
                    )
                ],
                narrative="",
                use_cases_impacted=[],
                finalize=False,
                reasoning="checking",
            ),
            _finalize(),
        ]
    )

    received: dict = {}

    def _make(repo_path, container, image):  # sync, matches make_blast_radius_tool
        @tool
        async def blast_radius(package_name: str, depth: int = 3) -> dict:
            """Fake blast_radius tool for testing."""
            received["depth"] = depth
            return {"package_name": package_name, "available": True}

        return blast_radius

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(impact_analysis_agent, "make_blast_radius_tool", _make),
    ):
        await impact_analysis_agent.analyze_impact(
            _finding(), _prep(), container=None, depth=5
        )

    assert received["depth"] == 5


@pytest.mark.asyncio
async def test_analyze_impact_degrades_gracefully_on_llm_outage():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM outage")
    )

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(impact_analysis_agent, "_MAX_ITERATIONS", 2),
    ):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(vector_store_id=""), container=None
        )

    assert summary.available is False
    assert summary.source == "unavailable"
    assert summary.narrative == ""


@pytest.mark.asyncio
async def test_make_impact_analysis_tool_wraps_analyze_impact_as_dict():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    fake_summary = BlastRadiusSummary(available=True, source="codegraph", narrative="x")
    with patch.object(
        impact_analysis_agent, "analyze_impact", AsyncMock(return_value=fake_summary)
    ):
        tool_fn = impact_analysis_agent.make_impact_analysis_tool(
            _finding(), _prep(), container=None
        )
        result = await tool_fn.ainvoke({"depth": 3})

    assert result["available"] is True
    assert result["narrative"] == "x"


@pytest.mark.asyncio
async def test_find_usage_sites_excludes_non_source_and_unmatched_files():
    from src.main_graph.subgraphs.report.agents.impact_analysis_agent import (
        _make_find_usage_sites_tool,
    )
    from src.main_graph.tools.search_code import _store_cache

    store = MagicMock()
    store.asimilarity_search = AsyncMock(
        return_value=[
            Document(page_content='import "left-pad"', metadata={"file": "src/a.ts"}),
            Document(
                page_content='{"dependencies": {"left-pad": "1.0.0"}}',
                metadata={"file": "package.json"},
            ),
            Document(page_content="unrelated code", metadata={"file": "src/b.ts"}),
        ]
    )
    _store_cache["vs-test"] = store
    try:
        tool_fn = _make_find_usage_sites_tool("vs-test")
        results = await tool_fn.ainvoke({"package_name": "left-pad"})
    finally:
        del _store_cache["vs-test"]

    assert len(results) == 1
    assert results[0]["file"] == "src/a.ts"
