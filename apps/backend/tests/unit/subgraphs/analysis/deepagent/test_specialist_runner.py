from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.deepagent.specialist_runner import (
    run_specialist,
)
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"chalk": "5.0.0"}, "packages": {}},
    )


def _make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        domain="maintenance",
        hypothesis="chalk may be unmaintained",
        packages_to_focus=["chalk"],
        findings=[
            FindingNote(
                dep_name="chalk",
                severity="low",
                description="stale",
                evidence=[EvidenceRef(tool="npm_outdated", url=None, log_snippet="")],
            )
        ],
        summary="1 finding",
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_run_specialist_runs_agent_saves_bundle_and_builds_record():
    dispatch = AgentDispatch(
        domain="maintenance",
        hypothesis="chalk may be unmaintained",
        packages_to_focus=["chalk"],
        agent_type="maintenance_agent",
    )
    fake_bundle = _make_bundle()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(return_value="bundle-123")
    svc = {"result_dao": fake_dao, "container": MagicMock(), "input_cache": None}

    with patch(
        "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
        ".MaintenanceAgent.run",
        new=AsyncMock(return_value=(fake_bundle, ["npm_outdated"], 1)),
    ):
        bundle_id, record = await run_specialist(
            "maintenance_agent", dispatch, _make_prep(), svc
        )

    assert bundle_id == "bundle-123"
    assert record["agent_type"] == "maintenance_agent"
    assert record["bundle_id"] == "bundle-123"
    assert record["packages_to_focus"] == ["chalk"]
    assert record["tools_used"] == ["npm_outdated"]
    fake_dao.save_bundle.assert_awaited_once_with(fake_bundle)
