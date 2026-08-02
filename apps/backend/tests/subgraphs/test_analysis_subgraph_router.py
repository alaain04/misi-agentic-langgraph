"""
Graph-level proof that the router actually routes:

1. A simple concern (vulnerability-only, no per-dependency requirement)
   never reaches analysis_deepagent_node -- if it did, the test would hang
   or error trying to reach a real LLM, since no deep-agent-specific mock
   is installed.
2. A complex concern with requires_per_dependency_analysis=False reaches
   save_analysis_result directly from coverage_gate, even though the deep
   agent never covered the sole direct dependency -- proving the new
   short-circuit (Task 9) is actually wired end-to-end, not just correct
   in isolation.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.analysis.graph import build_analysis_subgraph
from src.main_graph.subgraphs.analysis.nodes.handle_invalid_concern import (
    INVALID_CONCERN_MESSAGE,
)
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import EvidenceBundle, PrepResult


def _seed_prep(job_id: str) -> PrepResult:
    return PrepResult(
        job_id=job_id,
        repo_path="/tmp/test-repo",
        project_metadata={"name": "test-project"},
        manifest_files=["package.json", "package-lock.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
    )


_TRIVY_FIXTURE = {
    "SchemaVersion": 2,
    "Results": [
        {
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2021-23337",
                    "PkgName": "lodash",
                    "InstalledVersion": "4.17.20",
                    "FixedVersion": "4.17.21",
                    "Severity": "HIGH",
                    "Title": "prototype pollution in lodash < 4.17.21",
                    "Description": "Lodash prototype pollution vulnerability",
                    "PrimaryURL": "https://nvd.nist.gov/vuln/detail/CVE-2021-23337",
                }
            ]
        }
    ],
}


def _fake_concern_llm(concern: Concern) -> MagicMock:
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=concern
    )
    return mock_llm


@pytest.mark.asyncio
async def test_simple_concern_skips_deep_agent_entirely(subgraph_config, result_dao):
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_concern = Concern(
        is_valid=True,
        type=["vulnerability"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["vulnerability_agent"],
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(fake_concern),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.graph.analysis_deepagent_node",
            AsyncMock(side_effect=AssertionError("deep agent must not run")),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".trivy_vuln_scan",
            AsyncMock(return_value=_TRIVY_FIXTURE),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "check for known CVEs",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert len(analysis.evidence_bundle_ids) == 1
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 1
    assert agent_calls[0]["agent_type"] == "vulnerability_agent"


@pytest.mark.asyncio
async def test_complex_concern_without_per_dependency_requirement_skips_forced_coverage(
    subgraph_config, result_dao
):
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = MagicMock()
    fake_deep_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="No specialists needed, finalizing.")],
            "bundle_ids": [],
            "agent_calls": [],
        }
    )
    fake_concern = Concern(
        is_valid=True,
        type=["maintenance"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["maintenance_agent"],
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(fake_concern),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.graph.backstop_dispatch_node",
            AsyncMock(side_effect=AssertionError("backstop must not run")),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "how healthy is this project's dependency set overall?",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert analysis.evidence_bundle_ids == []
    assert analysis.findings == []


@pytest.mark.asyncio
async def test_mixed_concern_peels_off_vulnerability_before_deep_agent_runs(
    subgraph_config, result_dao
):
    """A mixed concern (vulnerability + maintenance) gets its
    vulnerability_agent portion run directly via the router's whole-tree
    prefix -- real Trivy call included -- BEFORE analysis_deepagent_node ever
    starts. The deep agent (faked here to return a canned maintenance
    finding, since only the routing/combination is under test) must be
    invoked with vulnerability_agent already excluded from its roster and
    told it's done, and the final result must combine both agents' findings
    without double-counting."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    maintenance_bundle_id = "bundle-maint"
    maintenance_call = {
        "agent_type": "maintenance_agent",
        "bundle_id": maintenance_bundle_id,
        "conductor_iteration": 0,
        "domain": "maintenance",
        "packages_to_focus": ["lodash"],
        "tools_used": ["npm_outdated"],
        "react_iterations": 1,
        "started_at": "2026-08-02T00:00:00Z",
        "finished_at": "2026-08-02T00:00:01Z",
    }
    await result_dao.save_bundle(
        EvidenceBundle(
            id=maintenance_bundle_id,
            domain="maintenance",
            hypothesis="is lodash maintained?",
            packages_to_focus=["lodash"],
            findings=[
                FindingNote(
                    dep_name="lodash",
                    severity="medium",
                    description="lodash is behind on releases",
                    evidence=[
                        EvidenceRef(tool="npm_outdated", url=None, log_snippet="")
                    ],
                )
            ],
            summary="1 finding",
            confidence=0.8,
        )
    )

    fake_deep_agent = MagicMock()
    fake_deep_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="Sufficient evidence, finalizing.")],
            "bundle_ids": [maintenance_bundle_id],
            "agent_calls": [maintenance_call],
        }
    )
    fake_concern = Concern(
        is_valid=True,
        type=["vulnerability", "maintenance"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["vulnerability_agent", "maintenance_agent"],
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(fake_concern),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".trivy_vuln_scan",
            AsyncMock(return_value=_TRIVY_FIXTURE),
        ) as mock_trivy_scan,
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "vulnerabilities and unmaintained dependencies",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    mock_trivy_scan.assert_awaited_once()
    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert len(analysis.evidence_bundle_ids) == 2
    assert len(set(analysis.evidence_bundle_ids)) == 2
    assert len(analysis.findings) == 2

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert {c["agent_type"] for c in agent_calls} == {
        "vulnerability_agent",
        "maintenance_agent",
    }

    # The deep agent must have been invoked with vulnerability_agent already
    # excluded from its available-specialists roster.
    deepagent_state_arg = fake_deep_agent.ainvoke.await_args.args[0]
    system_content = deepagent_state_arg["messages"][0].content
    assert "- vulnerability_agent:" not in system_content
    assert "- maintenance_agent:" in system_content
    assert (
        "Already completed for this concern: ['vulnerability_agent']" in system_content
    )


@pytest.mark.asyncio
async def test_invalid_concern_ends_the_subgraph_without_an_analysis_result(
    subgraph_config, result_dao
):
    """A concern the classifier flags as invalid (e.g. a greeting) must
    never reach run_direct_agents or analysis_deepagent_node, and must not
    set analysis_result_id -- main_graph's existing _after_analysis routing
    (`if not analysis_result_id: return END`) is what actually skips
    remediation/report for this job; this test only proves the analysis
    subgraph holds up its end of that contract."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_concern = Concern(
        is_valid=False,
        type=["other"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=[],
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(fake_concern),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.graph.run_direct_agents",
            AsyncMock(side_effect=AssertionError("run_direct_agents must not run")),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.graph.analysis_deepagent_node",
            AsyncMock(side_effect=AssertionError("deep agent must not run")),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "hello there",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id") is None

    job_repo = subgraph_config["configurable"]["job_repo"]
    job_repo.update_artifact_data.assert_awaited_once()
    call = job_repo.update_artifact_data.await_args
    assert call.args[0] == job_id
    assert call.args[1] == "analysis"
    assert call.args[2] == {"message": INVALID_CONCERN_MESSAGE}
