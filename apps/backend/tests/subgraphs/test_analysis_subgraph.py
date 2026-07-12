"""
Blackbox integration test for the analysis subgraph.

What is real:
- LangGraph fan-out/fan-in (agent_dispatcher → domain_agent → evidence_collector)
- save_analysis_result (MongoDB persistence via testcontainer)
- AnalysisState accumulation (bundle_ids, conductor_iteration)

What is mocked:
- analysis_conductor._llm (controlled decision sequence: dispatch then finalize)
- base_agent._llm (returns canned DomainAgentDecision with finalize=True)
- PrepResult is seeded directly into MongoDB (no discovery run needed)
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import (
    AgentDispatch,
    AnalysisConductorDecision,
    DomainAgentDecision,
    PrepResult,
)
from src.main_graph.subgraphs.analysis.graph import build_analysis_subgraph


def _seed_prep(job_id: str) -> PrepResult:
    return PrepResult(
        job_id=job_id,
        repo_path="/tmp/test-repo",
        project_metadata={
            "name": "test-project",
            "package_manager": "npm",
            "direct_dependencies_count": 3,
            "transitive_dependencies_count": 0,
        },
        manifest_files=["package.json", "package-lock.json"],
        detected_package_manager="npm",
        dependency_graph={
            "direct": {"lodash": "4.17.20", "express": "4.18.2", "axios": "1.6.0"},
            "packages": {},
        },
        discovery_summary="test-project depends on lodash, express, and axios.",
        vector_store_id="",
    )


def _make_conductor_llm(decisions: list[AnalysisConductorDecision]):
    """Mock analysis_conductor._llm to return decisions in sequence."""
    call_index = 0

    async def _ainvoke(messages):
        nonlocal call_index
        d = decisions[min(call_index, len(decisions) - 1)]
        call_index += 1
        return d

    chain = MagicMock()
    chain.ainvoke = _ainvoke
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm


def _make_agent_llm(decision: DomainAgentDecision):
    """Mock base_agent._llm to return the given decision every call."""
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=decision)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm


# vulnerability_agent is deterministic: it runs `npm audit` (not the LLM) and
# extracts every advisory. Feed it a canned audit result so the graph wiring can
# be exercised without a real repo.
_AUDIT_FIXTURE = {
    "advisories": {
        "1": {
            "module_name": "lodash", "severity": "high",
            "title": "CVE-2021-23337: prototype pollution in lodash < 4.17.21",
            "vulnerable_versions": "<4.17.21", "patched_versions": ">=4.17.21",
            "cves": ["CVE-2021-23337"], "url": None,
            "findings": [{"version": "4.17.20"}],
        }
    }
}


@pytest.mark.asyncio
async def test_analysis_dispatches_agent_and_saves_result(subgraph_config, result_dao):
    """
    Conductor dispatches one agent, domain_agent collects evidence,
    conductor finalizes — AnalysisResult with findings lands in MongoDB.
    """
    job_id = f"anal-{uuid.uuid4().hex[:8]}"

    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    dispatch = AgentDispatch(
        domain="vulnerability",
        hypothesis="Check for known CVEs in lodash 4.17.20",
        packages_to_focus=["lodash"],
        agent_type="vulnerability_agent",
    )

    conductor_decisions = [
        AnalysisConductorDecision(
            dispatches=[dispatch],
            finalize=False,
            reasoning="Checking vulnerabilities in lodash",
        ),
        AnalysisConductorDecision(
            dispatches=[],
            finalize=True,
            reasoning="Sufficient evidence collected",
        ),
    ]

    agent_decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="high",
                description="CVE-2021-23337: prototype pollution in lodash < 4.17.21",
                evidence=[EvidenceRef(tool="osv_lookup", url=None, log_snippet="GHSA-35jh-r3h4")],
            )
        ],
        summary="lodash 4.17.20 is vulnerable to CVE-2021-23337",
        confidence=0.9,
        finalize=True,
        reasoning="Found CVE, no further investigation needed",
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.analysis_conductor._llm",
            _make_conductor_llm(conductor_decisions),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            _make_agent_llm(agent_decision),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.npm_audit",
            AsyncMock(return_value=_AUDIT_FIXTURE),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "security vulnerabilities",
                "prep_result_id": prep.id,
                "bundle_ids": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id"), "Expected analysis_result_id in output state"

    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert analysis.job_id == job_id
    assert analysis.concern == "security vulnerabilities"
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"
    assert analysis.findings[0].severity == "high"
    assert len(analysis.evidence_bundle_ids) == 1


@pytest.mark.asyncio
async def test_analysis_finalizes_immediately_when_no_dispatches(subgraph_config, result_dao):
    """
    If conductor finalizes on the first call with no dispatches,
    the result is saved with empty findings.
    """
    job_id = f"anal-{uuid.uuid4().hex[:8]}"

    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    conductor_decisions = [
        AnalysisConductorDecision(
            dispatches=[],
            finalize=True,
            reasoning="Concern is not actionable for these packages",
        ),
    ]

    with patch(
        "src.main_graph.subgraphs.analysis.nodes.analysis_conductor._llm",
        _make_conductor_llm(conductor_decisions),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "none",
                "prep_result_id": prep.id,
                "bundle_ids": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert analysis.findings == []


@pytest.mark.asyncio
async def test_analysis_accumulates_bundles_from_parallel_agents(subgraph_config, result_dao):
    """
    Conductor dispatches two agents in parallel; both findings end up
    in the AnalysisResult (fan-in via Annotated[list, operator.add]).
    """
    job_id = f"anal-{uuid.uuid4().hex[:8]}"

    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    dispatches = [
        AgentDispatch(
            domain="vulnerability",
            hypothesis="CVEs in lodash",
            packages_to_focus=["lodash"],
            agent_type="vulnerability_agent",
        ),
        AgentDispatch(
            domain="maintenance",
            hypothesis="Is lodash maintained?",
            packages_to_focus=["lodash"],
            agent_type="maintenance_agent",
        ),
    ]

    conductor_decisions = [
        AnalysisConductorDecision(
            dispatches=dispatches, finalize=False, reasoning="parallel check"
        ),
        AnalysisConductorDecision(
            dispatches=[], finalize=True, reasoning="done"
        ),
    ]

    agent_decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="medium",
                description="Finding from parallel agent",
                evidence=[],
            )
        ],
        summary="One finding found",
        confidence=0.8,
        finalize=True,
        reasoning="done",
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.analysis_conductor._llm",
            _make_conductor_llm(conductor_decisions),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            _make_agent_llm(agent_decision),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.npm_audit",
            AsyncMock(return_value=_AUDIT_FIXTURE),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "bundle_ids": [],
            },
            config=subgraph_config,
        )

    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    # Two agents dispatched → two bundles. The maintenance agent contributes its
    # mocked finding; the deterministic vulnerability agent contributes the lodash
    # advisory from the audit fixture.
    assert len(analysis.evidence_bundle_ids) == 2
    assert len(analysis.findings) == 2
