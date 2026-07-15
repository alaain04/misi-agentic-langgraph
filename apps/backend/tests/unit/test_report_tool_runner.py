from __future__ import annotations

import pytest

from src.models.conductor import FindingNote
from src.models.results import AnalysisResult, PrepResult


def _prep() -> PrepResult:
    return PrepResult(
        job_id="j1", repo_path="/tmp/repo", project_metadata={},
        manifest_files=[], detected_package_manager="npm",
        dependency_graph={}, discovery_summary="", vector_store_id="",
    )


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        job_id="j1",
        concern="c",
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="high",
                description="d",
                evidence=[],
            ),
            FindingNote(
                dep_name="axios",
                severity="low",
                description="d2",
                evidence=[],
            ),
        ],
        evidence_bundle_ids=[],
        iteration_count=1,
    )


def test_registry_has_expected_tools():
    from src.main_graph.subgraphs.report.utils.registry import (
        REPORT_TOOL_DESCRIPTIONS,
        REPORT_TOOL_HANDLERS,
    )
    expected = {"web_search", "code_impact", "get_findings"}
    assert set(REPORT_TOOL_HANDLERS) == expected
    assert set(REPORT_TOOL_DESCRIPTIONS) == expected


@pytest.mark.asyncio
async def test_get_findings_handler_filters_by_severity():
    from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_HANDLERS
    handler = REPORT_TOOL_HANDLERS["get_findings"]
    result = await handler({"severity": "high"}, _prep(), _analysis())
    assert len(result["findings"]) == 1
    assert result["findings"][0]["dep_name"] == "lodash"


@pytest.mark.asyncio
async def test_get_findings_handler_all_returns_everything():
    from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_HANDLERS
    handler = REPORT_TOOL_HANDLERS["get_findings"]
    result = await handler({"severity": "all"}, _prep(), _analysis())
    assert len(result["findings"]) == 2
