from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.select_targets import (
    _has_no_upgrade,
    select_remediation_targets,
    select_targets_node,
)
from src.models.conductor import FindingNote
from src.models.results import PrepResult

_DEP_GRAPH = {"direct": {"lodash": "^4.17.11"}, "packages": {}}


def _no_blast_radius():
    return patch(
        "src.main_graph.subgraphs.remediation.select_targets.compute_blast_radius",
        AsyncMock(return_value={"available": False}),
    )


def _no_index(**overrides):
    return patch(
        "src.main_graph.subgraphs.remediation.select_targets._index_codegraph",
        AsyncMock(return_value=overrides.get("return_value", True)),
    )


def _prep(**overrides):
    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={},
        manifest_files=["package.json"],
        package_manager="npm",
        docker_image="node:lts-alpine",
        dependency_graph=_DEP_GRAPH,
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


def _finding(dep_name: str, severity: str = "high") -> FindingNote:
    return FindingNote(
        dep_name=dep_name, severity=severity, description="d", evidence=[]
    )


# --- select_remediation_targets (pure, deterministic) ----------------------


def test_select_remediation_targets_anchors_transitive_to_direct_parent():
    graph = {
        "direct": {"webpack": "5.0.0"},
        "packages": {"webpack@5.0.0": {"dependencies": ["qs@6.5.2"]}, "qs@6.5.2": {}},
    }
    targets = select_remediation_targets([_finding("qs")], graph, "low")
    assert len(targets) == 1
    assert targets[0].target_dep == "webpack"
    assert targets[0].addresses == ["qs"]


def test_select_remediation_targets_drops_finding_with_no_anchor():
    graph = {"direct": {}, "packages": {}}
    assert select_remediation_targets([_finding("orphan")], graph, "low") == []


def test_select_remediation_targets_filters_by_severity():
    targets = select_remediation_targets(
        [_finding("lodash", severity="low")], _DEP_GRAPH, "high"
    )
    assert targets == []


# --- _has_no_upgrade (pure) --------------------------------------------------


def test_has_no_upgrade_true_when_latest_at_or_below_floor():
    assert _has_no_upgrade("^4.17.11", "4.17.11") is True


def test_has_no_upgrade_false_when_upgrade_exists():
    assert _has_no_upgrade("^4.17.11", "4.17.21") is False


def test_has_no_upgrade_false_when_either_side_missing():
    assert _has_no_upgrade(None, "4.17.21") is False
    assert _has_no_upgrade("^4.17.11", None) is False


# --- select_targets_node -----------------------------------------------------


@pytest.mark.asyncio
async def test_select_targets_node_forces_r3_when_no_upgrade_exists():
    prep = _prep(dependency_graph={"direct": {"matcha": "0.7.0"}, "packages": {}})
    analysis = MagicMock(findings=[_finding("matcha")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=("0.7.0", None)),
        ),
        _no_blast_radius(),
        _no_index(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert result["targets"]["matcha"]["tier"] == "r3"
    assert result["remediations"] == {}


@pytest.mark.asyncio
async def test_select_targets_node_leaves_tier_unset_when_upgrade_exists():
    prep = _prep()
    analysis = MagicMock(findings=[_finding("lodash")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=("4.17.21", ("lodash", "lodash"))),
        ),
        _no_blast_radius(),
        _no_index(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert result["targets"]["lodash"]["tier"] is None
    assert result["investigations"]["lodash"]["release"]["migration_needed"] is False


@pytest.mark.asyncio
async def test_select_targets_node_survives_codegraph_index_failure():
    """A failed/unavailable codegraph init must not crash the whole node --
    targets/investigations must still populate, unlike the old classify.py
    bug this replaces."""
    prep = _prep()
    analysis = MagicMock(findings=[_finding("lodash")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    container = AsyncMock()
    container.run.side_effect = RuntimeError("docker daemon unreachable")
    config = {"configurable": {"result_dao": dao, "container": container}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=("4.17.21", ("lodash", "lodash"))),
        ),
        _no_blast_radius(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert result["targets"]["lodash"]["target_dep"] == "lodash"
    assert result["investigations"]["lodash"]["target_dep"] == "lodash"


@pytest.mark.asyncio
async def test_select_targets_node_populates_dependents_and_call_sites():
    graph = {
        "direct": {"webpack": "5.0.0"},
        "packages": {
            "webpack@5.0.0": {"dependencies": ["qs@6.5.2"]},
            "qs@6.5.2": {},
        },
    }
    prep = _prep(dependency_graph=graph)
    analysis = MagicMock(findings=[_finding("qs")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=(None, None)),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.compute_blast_radius",
            AsyncMock(
                return_value={"available": True, "affected_files": ["src/a.ts:3"]}
            ),
        ),
        _no_index(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    inv = result["investigations"]["webpack"]
    assert inv["call_sites"] == ["src/a.ts:3"]
    # webpack is itself a direct dep -- dependents_of("webpack") on this
    # 2-package graph is [] (nothing depends on webpack here); the point of
    # this test is that the field is wired, not any specific graph shape.
    assert inv["dependents"] == []


@pytest.mark.asyncio
async def test_select_targets_node_bounds_concurrency():
    n_targets = 20
    deps = [f"dep-{i}" for i in range(n_targets)]
    prep = _prep(
        dependency_graph={"direct": {d: "1.0.0" for d in deps}, "packages": {}}
    )
    analysis = MagicMock(findings=[_finding(d) for d in deps])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    current = 0
    peak = 0
    lock = asyncio.Lock()

    async def _fake_resolve(*args, **kwargs):
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1
        return (None, None)

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(side_effect=_fake_resolve),
        ),
        _no_blast_radius(),
        _no_index(),
    ):
        await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert peak <= 6, f"expected concurrency to be capped at 6, observed {peak}"
    assert peak > 1


@pytest.mark.asyncio
async def test_select_targets_node_no_findings_short_circuits():
    prep = _prep()
    analysis = MagicMock(findings=[])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    result = await select_targets_node(
        {
            "job_id": "job-1",
            "prep_result_id": "prep-1",
            "analysis_result_id": "a-1",
            "concern": "c",
        },
        config,
    )
    assert result == {"targets": {}, "investigations": {}, "remediations": {}}
