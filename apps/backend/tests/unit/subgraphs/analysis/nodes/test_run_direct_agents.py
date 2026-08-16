from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.nodes.run_direct_agents import (
    run_direct_agents,
)
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import EvidenceBundle, PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
    )


def _bundle(domain: str) -> EvidenceBundle:
    return EvidenceBundle(
        domain=domain,
        hypothesis="check for known CVEs",
        packages_to_focus=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="high",
                description=f"{domain} finding",
                evidence=[EvidenceRef(tool="trivy", url=None, log_snippet="")],
            )
        ],
        summary="1 finding",
        confidence=1.0,
    )


def _state(preferred_agents: list[str]) -> dict:
    return {
        "job_id": "job-1",
        "concern": "check for known CVEs",
        "prep_result_id": "prep-1",
        "structured_concern": {
            "is_valid": True,
            "type": ["vulnerability"],
            "scope": "all_dependencies",
            "packages": [],
            "requires_per_dependency_analysis": False,
            "preferred_agents": preferred_agents,
        },
    }


@pytest.mark.asyncio
async def test_run_direct_agents_single_agent():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(return_value="bundle-1")
    mock_get_services = MagicMock(
        return_value={
            "result_dao": fake_dao,
            "container": MagicMock(),
            "input_cache": None,
        }
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.run_direct_agents.get_services",
            mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".VulnerabilityAgent.run",
            new=AsyncMock(return_value=(_bundle("vulnerability"), ["trivy"], 1)),
        ),
    ):
        result = await run_direct_agents(
            _state(["vulnerability_agent"]), {"configurable": {}}
        )

    assert result["bundle_ids"] == ["bundle-1"]
    assert len(result["agent_calls"]) == 1
    assert result["agent_calls"][0]["agent_type"] == "vulnerability_agent"
    assert result["agent_calls"][0]["packages_to_focus"] == []


@pytest.mark.asyncio
async def test_run_direct_agents_both_agents_run_concurrently():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(side_effect=["bundle-vuln", "bundle-lic"])
    mock_get_services = MagicMock(
        return_value={
            "result_dao": fake_dao,
            "container": MagicMock(),
            "input_cache": None,
        }
    )

    concurrent = 0
    peak = 0

    async def _slow_vuln_run(*args, **kwargs):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return _bundle("vulnerability"), ["trivy"], 1

    async def _slow_lic_run(*args, **kwargs):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return _bundle("license"), ["license_collector"], 1

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.run_direct_agents.get_services",
            mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".VulnerabilityAgent.run",
            new=_slow_vuln_run,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.license_agent.LicenseAgent.run",
            new=_slow_lic_run,
        ),
    ):
        result = await run_direct_agents(
            _state(["vulnerability_agent", "license_agent"]), {"configurable": {}}
        )

    # Verify result shape (existing assertions)
    assert set(result["bundle_ids"]) == {"bundle-vuln", "bundle-lic"}
    assert len(result["agent_calls"]) == 2
    assert {c["agent_type"] for c in result["agent_calls"]} == {
        "vulnerability_agent",
        "license_agent",
    }

    # Verify concurrent execution: peak should be 2 (both agents running together)
    # If they ran sequentially, peak would never exceed 1
    assert peak == 2


@pytest.mark.asyncio
async def test_run_direct_agents_excludes_non_whole_tree_agents_from_a_mixed_concern():
    """A mixed concern (e.g. vulnerability + maintenance) only gets its
    whole-tree portion dispatched here -- maintenance_agent is package-scoped
    and belongs to the DeepAgent's investigation, not this deterministic
    prefix step."""
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(return_value="bundle-vuln")
    mock_get_services = MagicMock(
        return_value={
            "result_dao": fake_dao,
            "container": MagicMock(),
            "input_cache": None,
        }
    )
    state = {
        "job_id": "job-1",
        "concern": "vulnerabilities and unmaintained dependencies",
        "prep_result_id": "prep-1",
        "structured_concern": {
            "is_valid": True,
            "type": ["vulnerability", "maintenance"],
            "scope": "all_dependencies",
            "packages": [],
            "requires_per_dependency_analysis": False,
            "preferred_agents": ["vulnerability_agent", "maintenance_agent"],
        },
    }

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.run_direct_agents.get_services",
            mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".VulnerabilityAgent.run",
            new=AsyncMock(return_value=(_bundle("vulnerability"), ["trivy"], 1)),
        ) as mock_vuln_run,
        patch(
            "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
            ".MaintenanceAgent.run",
        ) as mock_maintenance_run,
    ):
        result = await run_direct_agents(state, {"configurable": {}})

    mock_vuln_run.assert_awaited_once()
    mock_maintenance_run.assert_not_called()
    assert result["bundle_ids"] == ["bundle-vuln"]
    assert len(result["agent_calls"]) == 1
    assert result["agent_calls"][0]["agent_type"] == "vulnerability_agent"


@pytest.mark.asyncio
async def test_run_direct_agents_skips_dao_fetch_when_no_whole_tree_agents_apply():
    """A concern with no whole-tree agents (e.g. a pure maintenance concern)
    must not touch the DAO at all -- there is nothing for run_direct_agents
    to do, and get_prep is only useful if a specialist actually runs."""
    mock_get_services = MagicMock(side_effect=AssertionError("must not be called"))
    state = {
        "job_id": "job-1",
        "concern": "how healthy is this project's dependency set?",
        "prep_result_id": "prep-1",
        "structured_concern": {
            "is_valid": True,
            "type": ["maintenance"],
            "scope": "all_dependencies",
            "packages": [],
            "requires_per_dependency_analysis": False,
            "preferred_agents": ["maintenance_agent"],
        },
    }

    with patch(
        "src.main_graph.subgraphs.analysis.nodes.run_direct_agents.get_services",
        mock_get_services,
    ):
        result = await run_direct_agents(state, {"configurable": {}})

    assert result == {"bundle_ids": [], "agent_calls": []}
