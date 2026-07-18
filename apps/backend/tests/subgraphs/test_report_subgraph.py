"""
Blackbox integration test for the report subgraph.

What is real:
- save_report_result (MongoDB persistence via testcontainer)
- ReportResult construction from LLM JSON response
- Severity-based overall_risk_level derivation (pure Python, no mocks)

What is mocked:
- report_conductor._llm (controlled finalize decision)
- save_report_result._llm (returns canned JSON report)
- AnalysisResult is seeded directly into MongoDB (no analysis run needed)
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.conductor import FindingNote
from src.models.results import AnalysisResult
from src.models.results import ReportConductorDecision
from src.main_graph.subgraphs.report.graph import build_report_subgraph


def _seed_analysis(
    job_id: str, findings: list[FindingNote] | None = None
) -> AnalysisResult:
    if findings is None:
        findings = [
            FindingNote(
                dep_name="lodash",
                severity="high",
                description="CVE-2021-23337: prototype pollution",
                evidence=[],
            ),
            FindingNote(
                dep_name="axios",
                severity="medium",
                description="SSRF risk in axios < 1.7",
                evidence=[],
            ),
        ]
    return AnalysisResult(
        job_id=job_id,
        concern="security vulnerabilities",
        findings=findings,
        evidence_bundle_ids=["bundle-1", "bundle-2"],
        iteration_count=2,
    )


def _make_conductor_llm(decision: ReportConductorDecision):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=decision)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm


def _make_save_llm(report_json: dict):
    response = MagicMock()
    response.content = json.dumps(report_json)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=response)
    return llm


@pytest.mark.asyncio
async def test_report_produces_report_result(subgraph_config, result_dao):
    """Conductor finalizes immediately → save_report_result writes a ReportResult."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"

    analysis = _seed_analysis(job_id)
    await result_dao.save_analysis(analysis)

    report_payload = {
        "executive_summary": "lodash has a known prototype pollution CVE. Update to 4.17.21.",
        "overall_risk_level": "high",
        "findings": [
            {
                "dep_name": "lodash",
                "severity": "high",
                "description": "CVE-2021-23337: prototype pollution",
                "recommendation": "Upgrade to lodash >= 4.17.21",
                "alternatives": [],
                "affected_files": [],
                "evidence": [],
            }
        ],
        "recommendations": ["Upgrade lodash to 4.17.21 immediately"],
    }

    with (
        patch(
            "src.main_graph.subgraphs.report.nodes.report_conductor._llm",
            _make_conductor_llm(
                ReportConductorDecision(
                    tool_calls=[], finalize=True, reasoning="no enrichment needed"
                )
            ),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.save_report_result._llm",
            _make_save_llm(report_payload),
        ),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "security vulnerabilities",
                "prep_result_id": "unused-for-report",
                "analysis_result_id": analysis.id,
                "tool_results": [],
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id"), "Expected report_result_id in output state"

    report = await result_dao.get_report(result["report_result_id"])
    assert report.job_id == job_id
    assert report.overall_risk_level == "high"
    assert len(report.findings) == 1
    assert report.findings[0].dep_name == "lodash"
    assert report.executive_summary
    assert len(report.recommendations) > 0


@pytest.mark.asyncio
async def test_report_overall_risk_derived_from_findings_on_llm_failure(
    subgraph_config, result_dao
):
    """
    When the LLM returns unparseable JSON, save_report_result falls back to
    mapping findings directly and derives overall_risk_level from severity.
    """
    job_id = f"rep-{uuid.uuid4().hex[:8]}"

    findings = [
        FindingNote(
            dep_name="express",
            severity="critical",
            description="RCE in express < 4.19",
            evidence=[],
        )
    ]
    analysis = _seed_analysis(job_id, findings=findings)
    await result_dao.save_analysis(analysis)

    broken_llm = MagicMock()
    broken_llm.ainvoke = AsyncMock(return_value=MagicMock(content="not-valid-json {"))

    with (
        patch(
            "src.main_graph.subgraphs.report.nodes.report_conductor._llm",
            _make_conductor_llm(
                ReportConductorDecision(tool_calls=[], finalize=True, reasoning="done")
            ),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.save_report_result._llm",
            broken_llm,
        ),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "RCE risk",
                "prep_result_id": "unused",
                "analysis_result_id": analysis.id,
                "tool_results": [],
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert report.overall_risk_level == "critical"
    assert len(report.findings) == 1
    assert report.findings[0].dep_name == "express"


@pytest.mark.asyncio
async def test_report_with_empty_findings(subgraph_config, result_dao):
    """When analysis has no findings, the report is saved with no findings and risk=none."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"

    analysis = _seed_analysis(job_id, findings=[])
    await result_dao.save_analysis(analysis)

    report_payload = {
        "executive_summary": "No significant risks found in this project.",
        "overall_risk_level": "none",
        "findings": [],
        "recommendations": [],
    }

    with (
        patch(
            "src.main_graph.subgraphs.report.nodes.report_conductor._llm",
            _make_conductor_llm(
                ReportConductorDecision(
                    tool_calls=[], finalize=True, reasoning="nothing to enrich"
                )
            ),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.save_report_result._llm",
            _make_save_llm(report_payload),
        ),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "general review",
                "prep_result_id": "unused",
                "analysis_result_id": analysis.id,
                "tool_results": [],
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert report.overall_risk_level == "none"
    assert report.findings == []
