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
from src.models.remediation import RemediationTarget, TargetInvestigation
from src.models.results import PrepResult

_DEP_GRAPH = {"direct": {"lodash": "^4.17.11"}, "packages": {}}


def _no_blast_radius():
    return patch(
        "src.main_graph.subgraphs.remediation.classify.compute_blast_radius",
        AsyncMock(return_value={"available": False}),
    )


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
        _no_blast_radius(),
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        classification, investigation = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine", _DEP_GRAPH
        )

    assert classification.tier == "r1"
    assert investigation.target_dep == "lodash"
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
        _no_blast_radius(),
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        classification, investigation = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine", _DEP_GRAPH
        )

    assert classification.tier == "r2"
    assert investigation.release.migration_needed is True


@pytest.mark.asyncio
async def test_classify_target_migration_digest_flows_into_investigation():
    """The merged LLM call's migration_needed/migration_guide/breaking_changes
    must land in the returned TargetInvestigation's release digest -- this is
    the data the planner reads, previously produced by a separate
    investigate_release LLM call."""
    target = RemediationTarget(
        target_dep="eslint",
        addresses=["eslint"],
        current_range="^7.0.0",
        latest_version="8.0.0",
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(
            tier="r2",
            rationale="flat config is a breaking change",
            migration_needed=True,
            migration_guide="Switch to eslint.config.js",
            breaking_changes=["flat config replaces .eslintrc"],
        )
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.compute_blast_radius",
            AsyncMock(
                return_value={"available": True, "affected_files": ["src/a.ts:3"]}
            ),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": True, "releases": []}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        classification, investigation = await classify_target(
            target,
            "/tmp/repo",
            MagicMock(),
            "node:lts-alpine",
            {"direct": {"eslint": "^7.0.0"}, "packages": {}},
        )

    assert classification.tier == "r2"
    assert investigation.call_sites == ["src/a.ts:3"]
    assert investigation.release.migration_needed is True
    assert investigation.release.migration_guide == "Switch to eslint.config.js"
    assert investigation.release.breaking_changes == ["flat config replaces .eslintrc"]
    assert investigation.release.to_version == "8.0.0"


@pytest.mark.asyncio
async def test_classify_target_forces_r3_when_registry_has_no_higher_version():
    """Regression (job 6a7773a7576d0efd7796aa8c, `matcha`): 0.7.0 was both
    the installed and the latest published version, so no same-package
    upgrade existed. The LLM read "no further releases" as a clean upgrade
    and tiered it for a bump. Registry truth now decides this without
    consulting the model at all -- and without fetching release notes,
    though dependents/call-sites are still computed since they're useful
    context for the replacement plan."""
    target = RemediationTarget(
        target_dep="matcha",
        addresses=["matcha"],
        current_range="0.7.0",
        latest_version="0.7.0",
    )
    mock_llm = MagicMock()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.compute_blast_radius",
            AsyncMock(
                return_value={"available": True, "affected_files": ["src/b.ts:1"]}
            ),
        ) as mock_blast,
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": True, "releases": []}),
        ) as mock_notes,
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        classification, investigation = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine", _DEP_GRAPH
        )

    assert classification.tier == "r3"
    assert "0.7.0" in classification.rationale
    # Decided from the registry alone -- no release-notes fetch, no LLM call.
    mock_notes.assert_not_called()
    mock_llm.with_structured_output.assert_not_called()
    # Blast radius IS still computed for r3 -- useful for the replace plan.
    mock_blast.assert_called_once()
    assert investigation.call_sites == ["src/b.ts:1"]


@pytest.mark.asyncio
async def test_classify_target_does_not_force_r3_when_upgrade_exists():
    """A real upgrade above the range floor must still reach the LLM."""
    target = RemediationTarget(
        target_dep="lodash",
        addresses=["lodash"],
        current_range="^4.17.11",
        latest_version="4.17.21",
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(tier="r1", rationale="patch releases")
    )
    with (
        _no_blast_radius(),
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": True, "releases": []}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        classification, _investigation = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine", _DEP_GRAPH
        )

    assert classification.tier == "r1"
    mock_llm.with_structured_output.assert_called_once()


@pytest.mark.asyncio
async def test_classify_target_unresolvable_latest_version_falls_back_to_llm():
    """A registry lookup that failed (latest_version None) must not be read
    as "no upgrade exists"."""
    target = RemediationTarget(
        target_dep="lodash",
        addresses=["lodash"],
        current_range="^4.17.11",
        latest_version=None,
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(tier="r2", rationale="breaking changes")
    )
    with (
        _no_blast_radius(),
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": True, "releases": []}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        classification, _investigation = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine", _DEP_GRAPH
        )

    assert classification.tier == "r2"


@pytest.mark.asyncio
async def test_classify_target_blast_radius_failure_degrades_to_empty_call_sites():
    """codegraph being unreachable/unindexed must not crash classification --
    call_sites degrades to empty, same as any other best-effort signal."""
    target = RemediationTarget(
        target_dep="lodash", addresses=["lodash"], current_range="^4.17.11"
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(tier="r1", rationale="patch only")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.compute_blast_radius",
            AsyncMock(side_effect=RuntimeError("docker down")),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        classification, investigation = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine", _DEP_GRAPH
        )

    assert classification.tier == "r1"
    assert investigation.call_sites == []


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


def _investigation(dep: str) -> TargetInvestigation:
    from src.models.remediation import ReleaseDigest

    return TargetInvestigation(
        target_dep=dep,
        dependents=[],
        call_sites=[],
        release=ReleaseDigest(
            from_version=None, to_version=None, migration_needed=False
        ),
    )


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
            FindingNote(
                dep_name="lodash", severity="high", description="d", evidence=[]
            ),
            FindingNote(
                dep_name="left-pad", severity="high", description="d2", evidence=[]
            ),
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    async def _fake_classify(
        target, repo_path, container, docker_image, dependency_graph, resolved_repo=None
    ):
        if target.target_dep == "left-pad":
            return (
                TargetClassification(tier="r3", rationale="abandoned"),
                _investigation("left-pad"),
            )
        return (
            TargetClassification(tier="r1", rationale="patch bump"),
            _investigation("lodash"),
        )

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
    assert set(result["investigations"]) == {"lodash", "left-pad"}


@pytest.mark.asyncio
async def test_classify_targets_node_bounds_concurrency():
    """classify_target fans out a docker exec, a `gh api` call, a codegraph
    call, and an LLM call per target. Without a concurrency cap, a repo with
    many findings sends that many simultaneous calls to each -- real risk of
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

    async def _fake_classify(
        target, repo_path, container, docker_image, dependency_graph, resolved_repo=None
    ):
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1
        return (
            TargetClassification(tier="r1", rationale="patch bump"),
            _investigation(target.target_dep),
        )

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
async def test_classify_targets_node_resolves_repo_once_and_reuses_it():
    """classify_targets_node used to spawn two npm view container calls per
    target: one for the latest version and, separately, one re-resolving
    the same package's GitHub repo inside fetch_release_notes. The single
    resolve_package_info result must now flow into fetch_release_notes_between
    instead of being re-resolved."""
    prep = _prep()
    analysis = MagicMock(
        findings=[
            FindingNote(
                dep_name="lodash", severity="high", description="d", evidence=[]
            )
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(tier="r1", rationale="patch bump")
    )

    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.resolve_package_info",
            AsyncMock(return_value=("4.17.21", ("lodash", "lodash"))),
        ) as mock_resolve,
        _no_blast_radius(),
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes_between",
            AsyncMock(return_value={"available": True, "releases": []}),
        ) as mock_notes,
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
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

    mock_resolve.assert_called_once()
    mock_notes.assert_called_once()
    assert mock_notes.call_args.args[-1] == ("lodash", "lodash")


@pytest.mark.asyncio
async def test_classify_targets_node_initializes_codegraph_index_before_classifying():
    """classify_target's blast radius lookup shells out to `codegraph impact`,
    which needs the index built first. That index is no longer built during
    discovery (nothing there consumed it) -- classify_targets_node must build
    it itself, before it starts dispatching classify_target calls."""
    prep = _prep()
    analysis = MagicMock(
        findings=[
            FindingNote(
                dep_name="lodash", severity="high", description="d", evidence=[]
            )
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    container = AsyncMock()
    container.run.return_value = (0, "", "")
    config = {"configurable": {"result_dao": dao, "container": container}}

    call_order: list[str] = []

    async def _fake_classify(*args, **kwargs):
        call_order.append("classify_target")
        return (
            TargetClassification(tier="r1", rationale="patch bump"),
            _investigation("lodash"),
        )

    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.resolve_package_info",
            AsyncMock(return_value=("4.17.21", ("lodash", "lodash"))),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.classify.classify_target",
            AsyncMock(side_effect=_fake_classify),
        ),
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

    container.run.assert_awaited_once()
    _, kwargs = container.run.call_args
    assert "codegraph init" in kwargs["command"]
    assert kwargs["volume"] == f"{prep.repo_path}:/workspace"
    assert kwargs["run_as_root"] is True
    assert call_order == ["classify_target"], (
        "codegraph init must complete before classify_target is dispatched"
    )


@pytest.mark.asyncio
async def test_classify_targets_node_survives_codegraph_index_failure():
    """A failed/unavailable codegraph init must not crash the whole classify
    step -- classify_target's own blast radius lookup already degrades
    gracefully to empty call sites."""
    prep = _prep()
    analysis = MagicMock(
        findings=[
            FindingNote(
                dep_name="lodash", severity="high", description="d", evidence=[]
            )
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    container = AsyncMock()
    container.run.side_effect = RuntimeError("docker daemon unreachable")
    config = {"configurable": {"result_dao": dao, "container": container}}

    with patch(
        "src.main_graph.subgraphs.remediation.classify.classify_target",
        AsyncMock(
            return_value=(
                TargetClassification(tier="r1", rationale="patch bump"),
                _investigation("lodash"),
            )
        ),
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

    assert result["targets"]["lodash"]["tier"] == "r1"


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
    assert result == {"targets": {}, "investigations": {}, "remediations": {}}
