"""Blackbox integration test for the report subgraph.

What is real:
- report_intake (severity filtering via MongoDB-backed AnalysisResult)
- report_synthesizer (MongoDB persistence via testcontainer)
- ReportResult construction, overall_risk_level derivation (pure Python)

What is mocked:
- finding_enricher_agent._llm (controlled per-finding decisions, matched to
  the finding by dep_name since parallel Send branches run in nondeterministic
  order)
- finding_enricher_agent.critique_report_finding (controlled verdicts)
- report_synthesizer._llm (returns canned executive_summary/recommendations)
- AnalysisResult is seeded directly into MongoDB (no analysis run needed)
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.report.agents import (
    finding_enricher_agent,
    impact_analysis_agent,
)
from src.main_graph.subgraphs.report.agents.critique import FindingVerdict
from src.main_graph.subgraphs.report.graph import build_report_subgraph
from src.models.conductor import FindingNote
from src.models.results import (
    AnalysisResult,
    FindingEnrichmentDecision,
    PrepResult,
    ReportFinding,
)


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


def _finalize(finding: ReportFinding) -> FindingEnrichmentDecision:
    return FindingEnrichmentDecision(
        tool_calls=[], finding=finding, finalize=True, reasoning="enriched"
    )


def _ok_verdict() -> FindingVerdict:
    return FindingVerdict(ok=True, feedback="", calibrated_confidence=0.9)


def _make_enricher_llm(decisions_by_dep: dict[str, FindingEnrichmentDecision]):
    async def _ainvoke(messages):
        system = messages[0]["content"]
        for dep, decision in decisions_by_dep.items():
            if f"package: {dep}" in system:
                return decision
        raise AssertionError(
            f"no mocked decision matched system prompt: {system[:200]}"
        )

    chain = MagicMock()
    chain.ainvoke = AsyncMock(side_effect=_ainvoke)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm


def _make_synthesizer_llm(payload: dict):
    response = MagicMock()
    response.content = json.dumps(payload)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=response)
    return llm


@pytest.mark.asyncio
async def test_report_produces_report_result_with_trusted_findings(
    subgraph_config, result_dao
):
    job_id = f"rep-{uuid.uuid4().hex[:8]}"
    analysis = _seed_analysis(job_id)
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
        codegraph_ready=False,
    )
    prep_result_id = await result_dao.save_prep(prep)

    decisions = {
        "lodash": _finalize(
            ReportFinding(
                dep_name="lodash",
                severity="high",
                description="CVE-2021-23337: prototype pollution",
                recommendation="Upgrade to lodash >= 4.17.21",
            )
        ),
        "axios": _finalize(
            ReportFinding(
                dep_name="axios",
                severity="medium",
                description="SSRF risk in axios < 1.7",
                recommendation="Upgrade to axios >= 1.7",
            )
        ),
    }
    synth_payload = {
        "executive_summary": "lodash and axios both need upgrades.",
        "recommendations": ["Upgrade lodash and axios"],
    }

    with (
        patch.object(finding_enricher_agent, "_llm", _make_enricher_llm(decisions)),
        patch.object(
            finding_enricher_agent,
            "critique_report_finding",
            AsyncMock(return_value=_ok_verdict()),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
            _make_synthesizer_llm(synth_payload),
        ),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "security vulnerabilities",
                "prep_result_id": prep_result_id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert report.job_id == job_id
    assert len(report.findings) == 2
    assert all(f.trust for f in report.findings)
    assert report.overall_risk_level == "high"
    assert report.executive_summary


@pytest.mark.asyncio
async def test_report_with_empty_findings(subgraph_config, result_dao):
    job_id = f"rep-{uuid.uuid4().hex[:8]}"
    analysis = _seed_analysis(job_id, findings=[])
    await result_dao.save_analysis(analysis)

    synth_payload = {
        "executive_summary": "No significant risks found in this project.",
        "recommendations": [],
    }

    with patch(
        "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
        _make_synthesizer_llm(synth_payload),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "general review",
                "prep_result_id": "unused",
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert report.overall_risk_level == "none"
    assert report.findings == []


@pytest.mark.asyncio
async def test_report_drops_low_severity_findings_before_enrichment(
    subgraph_config, result_dao
):
    """risk_min_severity="high" filters out the medium finding in
    report_intake, before it ever reaches a finding_enricher — only the
    high finding's decision needs to be mocked."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"
    findings = [
        FindingNote(
            dep_name="lodash", severity="high", description="high risk", evidence=[]
        ),
        FindingNote(
            dep_name="axios", severity="medium", description="medium risk", evidence=[]
        ),
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
        codegraph_ready=False,
    )
    prep_result_id = await result_dao.save_prep(prep)

    decisions = {
        "lodash": _finalize(
            ReportFinding(
                dep_name="lodash",
                severity="high",
                description="high risk",
                recommendation="Upgrade lodash",
            )
        )
    }
    synth_payload = {"executive_summary": "lodash is high risk.", "recommendations": []}

    with (
        patch.object(finding_enricher_agent, "_llm", _make_enricher_llm(decisions)),
        patch.object(
            finding_enricher_agent,
            "critique_report_finding",
            AsyncMock(return_value=_ok_verdict()),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
            _make_synthesizer_llm(synth_payload),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_intake.settings"
        ) as mock_settings,
    ):
        mock_settings.risk_min_severity = "high"
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "security vulnerabilities",
                "prep_result_id": prep_result_id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert len(report.findings) == 1
    assert report.findings[0].dep_name == "lodash"


@pytest.mark.asyncio
async def test_report_keeps_untrusted_finding_instead_of_dropping(
    subgraph_config, result_dao
):
    """A finding whose evidence fails critique stays in the report, flagged
    trust=False with the critique feedback as observation — never dropped."""
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
        codegraph_ready=False,
    )
    prep_result_id = await result_dao.save_prep(prep)

    decisions = {
        "left-pad": _finalize(
            ReportFinding(
                dep_name="left-pad",
                severity="high",
                description="GPL-incompatible copyleft dependency",
                recommendation="Remove or replace left-pad",
            )
        )
    }
    synth_payload = {
        "executive_summary": "left-pad is GPL-incompatible.",
        "recommendations": [],
    }

    with (
        patch.object(finding_enricher_agent, "_llm", _make_enricher_llm(decisions)),
        patch.object(finding_enricher_agent, "_MAX_ITERATIONS", 1),
        patch.object(
            finding_enricher_agent,
            "critique_report_finding",
            AsyncMock(
                return_value=FindingVerdict(
                    ok=False,
                    feedback="business_impact is not grounded in tool output",
                    calibrated_confidence=0.2,
                )
            ),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
            _make_synthesizer_llm(synth_payload),
        ),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "license compliance",
                "prep_result_id": prep_result_id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert len(report.findings) == 1  # kept, not dropped
    finding = report.findings[0]
    assert finding.trust is False
    assert finding.observation == "business_impact is not grounded in tool output"


@pytest.mark.asyncio
async def test_report_grounds_blast_radius_via_codegraph(subgraph_config, result_dao):
    """finding_enricher's impact_analysis tool call -> nested agent's own
    blast_radius call -> container port -> the resulting draft's
    blast_radius/affected_files come from the real tool output, not either
    LLM's placeholder text."""
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

    from src.models.conductor import ToolCall
    from src.models.results import ImpactAnalysisDecision

    outer_tool_call_decision = FindingEnrichmentDecision(
        tool_calls=[
            ToolCall(tool="impact_analysis", args={}, reason="check real usage")
        ],
        finding=None,
        finalize=False,
        reasoning="enrich with impact analysis",
    )
    outer_final_decision = _finalize(
        ReportFinding(
            dep_name="left-pad",
            severity="high",
            description="GPL-incompatible copyleft dependency",
            recommendation="Remove or replace left-pad",
            affected_files=["should-be-overwritten.js:1"],
            business_impact="Only used in a build script, never shipped.",
        )
    )

    mock_outer_llm = MagicMock()
    mock_outer_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[outer_tool_call_decision, outer_final_decision]
    )

    inner_tool_call_decision = ImpactAnalysisDecision(
        tool_calls=[
            ToolCall(tool="blast_radius", args={}, reason="check graph depth")
        ],
        narrative="",
        use_cases_impacted=[],
        finalize=False,
        reasoning="checking",
    )
    inner_final_decision = ImpactAnalysisDecision(
        tool_calls=[],
        narrative="Only used in a build script, never shipped.",
        use_cases_impacted=[],
        finalize=True,
        reasoning="done",
    )
    mock_inner_llm = MagicMock()
    mock_inner_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[inner_tool_call_decision, inner_final_decision]
    )

    synth_payload = {
        "executive_summary": "left-pad is GPL-incompatible but low exposure.",
        "recommendations": ["Replace left-pad with String.prototype.padStart"],
    }

    with (
        patch.object(finding_enricher_agent, "_llm", mock_outer_llm),
        patch.object(impact_analysis_agent, "_llm", mock_inner_llm),
        patch.object(
            finding_enricher_agent,
            "critique_report_finding",
            AsyncMock(return_value=_ok_verdict()),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
            _make_synthesizer_llm(synth_payload),
        ),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "license compliance",
                "prep_result_id": prep_result_id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    finding = report.findings[0]
    assert finding.blast_radius is not None
    assert finding.blast_radius.available is True
    assert finding.blast_radius.source == "codegraph"
    assert finding.blast_radius.isolated_to_tests_or_scripts is True
    assert (
        finding.blast_radius.narrative == "Only used in a build script, never shipped."
    )
    # grounded from the real tool output, not either LLM's placeholder text
    assert finding.affected_files == ["scripts/build.js:1"]
