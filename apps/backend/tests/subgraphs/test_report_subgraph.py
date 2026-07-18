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

from src.main_graph.subgraphs.report.graph import build_report_subgraph
from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import AnalysisResult, PrepResult, ReportConductorDecision


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


def _make_conductor_llm_sequence(decisions: list[ReportConductorDecision]):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(side_effect=decisions)
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
        "executive_summary": (
            "lodash has a known prototype pollution CVE. Update to 4.17.21."
        ),
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
    """When analysis has no findings, the report is saved with no findings and
    risk=none."""
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


@pytest.mark.asyncio
async def test_report_grounds_blast_radius_via_codegraph(subgraph_config, result_dao):
    """conductor calls blast_radius -> report_tool_runner invokes the tool via
    the container port -> save_report_result overwrites affected_files/
    blast_radius from the real tool output (not the LLM's text)."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"

    findings = [
        FindingNote(
            dep_name="left-pad",
            severity="high",
            description="GPL-incompatible copyleft dependency",
            evidence=[],
        )
    ]
    analysis = _seed_analysis(job_id, findings=findings)
    await result_dao.save_analysis(analysis)

    prep = PrepResult(
        job_id=job_id,
        repo_path="/tmp/fake-repo",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={},
        discovery_summary="",
        vector_store_id="",
        codegraph_ready=True,
    )
    prep_result_id = await result_dao.save_prep(prep)

    codegraph_output = {
        "symbol": "left-pad",
        "depth": 3,
        "nodeCount": 1,
        "edgeCount": 0,
        "affected": [
            {
                "name": "left-pad",
                "kind": "import",
                "filePath": "scripts/build.js",
                "startLine": 1,
            }
        ],
    }
    subgraph_config["configurable"]["container"].run = AsyncMock(
        return_value=(0, json.dumps(codegraph_output), "")
    )

    report_payload = {
        "executive_summary": "left-pad is GPL-incompatible but low exposure.",
        "overall_risk_level": "high",
        "findings": [
            {
                "dep_name": "left-pad",
                "severity": "high",
                "description": "GPL-incompatible copyleft dependency",
                "recommendation": "Remove or replace left-pad",
                "alternatives": [],
                "affected_files": ["should-be-overwritten.js:1"],
                "business_impact": "Only used in a build script, never shipped.",
                "evidence": [],
            }
        ],
        "recommendations": ["Replace left-pad with String.prototype.padStart"],
    }

    with (
        patch(
            "src.main_graph.subgraphs.report.nodes.report_conductor._llm",
            _make_conductor_llm_sequence(
                [
                    ReportConductorDecision(
                        tool_calls=[
                            ToolCall(
                                tool="blast_radius",
                                args={"package_name": "left-pad"},
                                reason="check real usage depth",
                            )
                        ],
                        finalize=False,
                        reasoning="enrich with blast radius",
                    ),
                    ReportConductorDecision(
                        tool_calls=[], finalize=True, reasoning="enriched"
                    ),
                ]
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
                "concern": "license compliance",
                "prep_result_id": prep_result_id,
                "analysis_result_id": analysis.id,
                "tool_results": [],
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    finding = report.findings[0]
    assert finding.blast_radius is not None
    assert finding.blast_radius.available is True
    assert finding.blast_radius.affected_file_count == 1
    assert finding.blast_radius.isolated_to_tests_or_scripts is True
    # grounded from the real tool output, not the LLM's placeholder text
    assert finding.affected_files == ["scripts/build.js:1"]


@pytest.mark.asyncio
async def test_report_drops_findings_below_min_severity(subgraph_config, result_dao):
    """report_min_severity="high" discards medium/low findings from the saved
    report, independent of vuln_min_severity (which only gates npm-audit CVEs)."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"

    findings = [
        FindingNote(
            dep_name="lodash", severity="high", description="high risk", evidence=[]
        ),
        FindingNote(
            dep_name="axios",
            severity="medium",
            description="medium risk",
            evidence=[],
        ),
    ]
    analysis = _seed_analysis(job_id, findings=findings)
    await result_dao.save_analysis(analysis)

    report_payload = {
        "executive_summary": "lodash is high risk, axios is medium risk.",
        "overall_risk_level": "high",
        "findings": [
            {
                "dep_name": "lodash",
                "severity": "high",
                "description": "high risk",
                "recommendation": "Upgrade lodash",
                "alternatives": [],
                "affected_files": [],
                "evidence": [],
            },
            {
                "dep_name": "axios",
                "severity": "medium",
                "description": "medium risk",
                "recommendation": "Upgrade axios",
                "alternatives": [],
                "affected_files": [],
                "evidence": [],
            },
        ],
        "recommendations": [],
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
        patch(
            "src.main_graph.subgraphs.report.nodes.save_report_result.settings"
        ) as mock_settings,
    ):
        mock_settings.report_min_severity = "high"
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

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert len(report.findings) == 1
    assert report.findings[0].dep_name == "lodash"


@pytest.mark.asyncio
async def test_report_strips_alternative_that_is_itself_a_flagged_dependency(
    subgraph_config, result_dao
):
    """The LLM suggested zod as an alternative to class-transformer, but zod is
    also flagged as a finding in this same report — that's self-contradictory,
    so the alternative must be stripped even though the LLM proposed it."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"

    findings = [
        FindingNote(
            dep_name="class-transformer",
            severity="high",
            description="prototype pollution risk",
            evidence=[],
        ),
        FindingNote(
            dep_name="zod",
            severity="medium",
            description="unmaintained for 18 months",
            evidence=[],
        ),
    ]
    analysis = _seed_analysis(job_id, findings=findings)
    await result_dao.save_analysis(analysis)

    tool_results_state = [
        ToolResult(
            id="tr-1",
            tool="web_search",
            args={"query": "class-transformer alternatives"},
            output={"results": [{"title": "zod vs class-transformer"}]},
            error=None,
            duration_ms=1,
        )
    ]

    report_payload = {
        "executive_summary": "class-transformer has a prototype pollution risk.",
        "overall_risk_level": "high",
        "findings": [
            {
                "dep_name": "class-transformer",
                "severity": "high",
                "description": "prototype pollution risk",
                "recommendation": "Replace with zod",
                "alternatives": ["zod"],
                "affected_files": [],
                "evidence": [],
            },
            {
                "dep_name": "zod",
                "severity": "medium",
                "description": "unmaintained for 18 months",
                "recommendation": "Monitor for a maintenance fork",
                "alternatives": [],
                "affected_files": [],
                "evidence": [],
            },
        ],
        "recommendations": [],
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
                "tool_results": tool_results_state,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    finding = next(f for f in report.findings if f.dep_name == "class-transformer")
    assert finding.alternatives == []


@pytest.mark.asyncio
async def test_report_overall_risk_reflects_filtered_findings(
    subgraph_config, result_dao
):
    """overall_risk_level is derived from the post-filter findings, not the raw
    analysis findings — filtering everything out yields risk=none, not the
    severity of a finding that was dropped from the report."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"

    findings = [
        FindingNote(
            dep_name="axios", severity="medium", description="medium risk", evidence=[]
        ),
    ]
    analysis = _seed_analysis(job_id, findings=findings)
    await result_dao.save_analysis(analysis)

    report_payload = {
        "executive_summary": "axios is medium risk.",
        "overall_risk_level": "medium",
        "findings": [
            {
                "dep_name": "axios",
                "severity": "medium",
                "description": "medium risk",
                "recommendation": "Upgrade axios",
                "alternatives": [],
                "affected_files": [],
                "evidence": [],
            },
        ],
        "recommendations": [],
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
        patch(
            "src.main_graph.subgraphs.report.nodes.save_report_result.settings"
        ) as mock_settings,
    ):
        mock_settings.report_min_severity = "critical"
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

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert report.findings == []
    assert report.overall_risk_level == "none"
