from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.deepagent.backstop import (
    deterministic_backstop_dispatch,
)
from src.models.results import EvidenceBundle, PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={
            "direct": {"chalk": "5.0.0", "uuid": "9.0.0"},
            "packages": {},
        },
        discovery_summary="a test repo",
        vector_store_id="",
    )


def _make_bundle(dep: str) -> EvidenceBundle:
    return EvidenceBundle(
        domain="backstop",
        hypothesis=f"deterministic coverage for {dep}",
        packages_to_focus=[dep],
        findings=[],
        summary="no findings",
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_backstop_covers_every_missing_dep_with_agent_types_already_used():
    agent_calls = [
        {"agent_type": "web_research_agent", "packages_to_focus": ["already-covered"]},
    ]
    prep = _make_prep()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(side_effect=["bundle-chalk", "bundle-uuid"])

    async def fake_run(self, dispatch, *args, **kwargs):
        return _make_bundle(dispatch.packages_to_focus[0]), [], 1

    with patch.object(REGISTRY["web_research_agent"], "run", new=fake_run):
        bundle_ids, new_calls = await deterministic_backstop_dispatch(
            missing_deps=["chalk", "uuid"],
            agent_calls=agent_calls,
            prep=prep,
            container=MagicMock(),
            dao=fake_dao,
            cache=None,
            concern="license and maintenance risk",
        )

    assert bundle_ids == ["bundle-chalk", "bundle-uuid"]
    assert [c["agent_type"] for c in new_calls] == [
        "web_research_agent",
        "web_research_agent",
    ]
    assert [c["bundle_id"] for c in new_calls] == ["bundle-chalk", "bundle-uuid"]


@pytest.mark.asyncio
async def test_backstop_defaults_to_web_research_agent_if_no_package_scoped_agent_ran():
    prep = _make_prep()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(return_value="bundle-1")

    async def fake_run(self, dispatch, *args, **kwargs):
        return _make_bundle(dispatch.packages_to_focus[0]), [], 1

    with patch.object(REGISTRY["web_research_agent"], "run", new=fake_run):
        bundle_ids, new_calls = await deterministic_backstop_dispatch(
            missing_deps=["chalk"],
            agent_calls=[],  # no whole-tree, no package-scoped calls at all
            prep=prep,
            container=MagicMock(),
            dao=fake_dao,
            cache=None,
            concern="license and maintenance risk",
        )

    assert bundle_ids == ["bundle-1"]
    assert new_calls[0]["agent_type"] == "web_research_agent"


@pytest.mark.asyncio
async def test_backstop_failure_on_one_dep_does_not_block_the_rest():
    prep = _make_prep()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(return_value="bundle-uuid")

    call_count = {"n": 0}

    async def fake_run(self, dispatch, *args, **kwargs):
        call_count["n"] += 1
        if dispatch.packages_to_focus[0] == "chalk":
            raise RuntimeError("boom")
        return _make_bundle(dispatch.packages_to_focus[0]), [], 1

    with patch.object(REGISTRY["web_research_agent"], "run", new=fake_run):
        bundle_ids, new_calls = await deterministic_backstop_dispatch(
            missing_deps=["chalk", "uuid"],
            agent_calls=[{"agent_type": "web_research_agent", "packages_to_focus": []}],
            prep=prep,
            container=MagicMock(),
            dao=fake_dao,
            cache=None,
            concern="x",
        )

    assert call_count["n"] == 2  # both attempted
    assert bundle_ids == ["bundle-uuid"]  # only the surviving one persisted
    assert len(new_calls) == 1
