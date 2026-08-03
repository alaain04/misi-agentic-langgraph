from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.classify import (
    TargetClassification,
    classify_target,
    classify_targets_node,
)
from src.models.conductor import FindingNote
from src.models.remediation import RemediationTarget
from src.models.results import PrepResult


@pytest.mark.asyncio
async def test_classify_target_returns_llm_classification():
    target = RemediationTarget(
        target_dep="lodash", addresses=["lodash"], current_range="^4.17.11"
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(tier="r1", rationale="patch release only")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        result = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine"
        )

    assert result.tier == "r1"
    mock_llm.with_structured_output.assert_called_once()


@pytest.mark.asyncio
async def test_classify_target_defaults_to_r2_on_llm_exception():
    """A classification failure must degrade to a conservative default
    (r2: assume breaking, needs review) rather than crashing the whole
    classify_targets_node and thus the job."""
    target = RemediationTarget(target_dep="lodash", addresses=["lodash"])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM provider timeout")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        result = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine"
        )

    assert result.tier == "r2"


def _prep(**overrides):
    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        docker_image="node:lts-alpine",
        dependency_graph={"direct": {"lodash": "^4.17.11"}, "packages": {}},
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


@pytest.mark.asyncio
async def test_classify_targets_node_carries_tier_hint_no_r3_settle():
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "^4.17.11", "left-pad": "1.0.0"},
            "packages": {},
        }
    )
    analysis = MagicMock(
        findings=[
            FindingNote(dep_name="lodash", severity="high", description="d", evidence=[]),
            FindingNote(
                dep_name="left-pad", severity="high", description="d2", evidence=[]
            ),
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    async def _fake_classify(target, repo_path, container, docker_image):
        if target.target_dep == "left-pad":
            return TargetClassification(tier="r3", rationale="abandoned")
        return TargetClassification(tier="r1", rationale="patch bump")

    with patch(
        "src.main_graph.subgraphs.remediation.classify.classify_target",
        AsyncMock(side_effect=_fake_classify),
    ):
        result = await classify_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    # Every target flows through (r3 is NOT settled here anymore).
    assert set(result["targets"]) == {"lodash", "left-pad"}
    assert result["remediations"] == {}
    assert result["targets"]["left-pad"]["tier"] == "r3"
    assert result["targets"]["lodash"]["tier"] == "r1"


@pytest.mark.asyncio
async def test_classify_targets_node_bounds_concurrency():
    """classify_target fans out a docker exec, a `gh api` subprocess, and an
    LLM call per target. Without a concurrency cap, a repo with many
    findings sends that many simultaneous calls to each -- real risk of
    provider rate-limiting (429s), a documented recurring problem in this
    project. This proves classify_targets_node bounds the number of
    concurrent in-flight classify_target calls to a small fixed cap."""
    n_targets = 20
    deps = [f"dep-{i}" for i in range(n_targets)]
    prep = _prep(
        dependency_graph={
            "direct": {dep: "1.0.0" for dep in deps},
            "packages": {},
        }
    )
    analysis = MagicMock(
        findings=[
            FindingNote(dep_name=dep, severity="high", description="d", evidence=[])
            for dep in deps
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    current = 0
    peak = 0
    lock = asyncio.Lock()

    async def _fake_classify(target, repo_path, container, docker_image):
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1
        return TargetClassification(tier="r1", rationale="patch bump")

    with patch(
        "src.main_graph.subgraphs.remediation.classify.classify_target",
        AsyncMock(side_effect=_fake_classify),
    ):
        await classify_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert peak <= 6, f"expected concurrency to be capped at 6, observed {peak}"
    assert peak > 1, "sanity check: some concurrency should still occur"


@pytest.mark.asyncio
async def test_classify_targets_node_no_findings_short_circuits():
    prep = _prep()
    analysis = MagicMock(findings=[])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    result = await classify_targets_node(
        {
            "job_id": "job-1",
            "prep_result_id": "prep-1",
            "analysis_result_id": "a-1",
            "concern": "c",
        },
        config,
    )
    assert result == {"targets": {}, "remediations": {}}
