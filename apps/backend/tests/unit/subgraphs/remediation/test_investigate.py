from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.investigate import (
    investigate_call_sites,
    investigate_dependents,
    investigate_release,
    investigate_target,
)
from src.models.remediation import ReleaseDigest, RemediationTarget, TargetInvestigation


@pytest.mark.asyncio
async def test_investigate_release_returns_digest_from_llm():
    notes = {
        "available": True,
        "releases": [{"tag": "v2.0.0", "body": "removed foo()"}],
    }
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=ReleaseDigest(
            from_version="1.0.0",
            to_version="2.0.0",
            migration_needed=True,
            migration_guide="replace foo() with bar()",
            breaking_changes=["foo() removed"],
        )
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.investigate.fetch_release_notes_between",
            AsyncMock(return_value=notes),
        ),
        patch("src.main_graph.subgraphs.remediation.investigate._llm", mock_llm),
    ):
        digest = await investigate_release(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", MagicMock(), "img"
        )
    assert digest.migration_needed is True
    assert digest.from_version == "1.0.0"
    assert digest.to_version == "2.0.0"


@pytest.mark.asyncio
async def test_investigate_release_conservative_on_failure():
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM timeout")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.investigate.fetch_release_notes_between",
            AsyncMock(return_value={"available": True, "releases": []}),
        ),
        patch("src.main_graph.subgraphs.remediation.investigate._llm", mock_llm),
    ):
        digest = await investigate_release(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", MagicMock(), "img"
        )
    assert digest.migration_needed is True  # conservative default
    assert digest.breaking_changes  # carries an explanatory reason


@pytest.mark.asyncio
async def test_investigate_release_conservative_when_notes_unavailable():
    mock_llm = MagicMock()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.investigate.fetch_release_notes_between",
            AsyncMock(return_value={"available": False, "error": "gh CLI not found"}),
        ),
        patch("src.main_graph.subgraphs.remediation.investigate._llm", mock_llm),
    ):
        digest = await investigate_release(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", MagicMock(), "img"
        )
    assert digest.migration_needed is True
    assert digest.breaking_changes
    assert "unavailable" in digest.breaking_changes[0]
    # LLM should not have been consulted for unavailable notes.
    mock_llm.with_structured_output.assert_not_called()


def test_investigate_dependents_uses_graph():
    graph = {
        "direct": {"eslint": "8.0.0"},
        "packages": {
            "eslint@8.0.0": {"version": "8.0.0", "dependencies": ["debug@4.0.0"]},
            "debug@4.0.0": {"version": "4.0.0", "dependencies": []},
        },
    }
    assert investigate_dependents(graph, "debug") == ["eslint"]


def test_investigate_call_sites_scans_repo(tmp_path):
    (tmp_path / "a.ts").write_text("import _ from 'lodash'\n_.map([])\n")
    (tmp_path / "b.ts").write_text("no usage here\n")
    sites = investigate_call_sites(str(tmp_path), "lodash")
    assert sites == ["a.ts"]


@pytest.mark.asyncio
async def test_investigate_target_combines_all_three():
    graph = {"direct": {"lodash": "4.17.15"}, "packages": {}}
    target = RemediationTarget(
        target_dep="lodash", addresses=["lodash"], current_range="^4.17.15", tier="r2"
    )
    with patch(
        "src.main_graph.subgraphs.remediation.investigate.investigate_release",
        AsyncMock(
            return_value=ReleaseDigest(
                from_version="4.17.15", to_version=None, migration_needed=False
            )
        ),
    ):
        inv = await investigate_target(
            target, "/tmp/repo", graph, MagicMock(), "img"
        )
    assert isinstance(inv, TargetInvestigation)
    assert inv.target_dep == "lodash"
    assert inv.release.migration_needed is False


@pytest.mark.asyncio
async def test_investigate_node_fans_out_and_bounds_concurrency():
    n = 20
    deps = [f"dep-{i}" for i in range(n)]
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        dependency_graph={"direct": {d: "1.0.0" for d in deps}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}
    targets = {
        d: RemediationTarget(target_dep=d, addresses=[d], tier="r1").model_dump()
        for d in deps
    }

    current = 0
    peak = 0
    lock = asyncio.Lock()

    async def _fake_investigate(target, repo_path, graph, container, image):
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1
        return TargetInvestigation(
            target_dep=target.target_dep,
            release=ReleaseDigest(
                from_version=None, to_version=None, migration_needed=False
            ),
        )

    with patch(
        "src.main_graph.subgraphs.remediation.investigate.investigate_target",
        AsyncMock(side_effect=_fake_investigate),
    ):
        from src.main_graph.subgraphs.remediation.investigate import (
            investigate_node,
        )

        out = await investigate_node(
            {"job_id": "j", "prep_result_id": "p", "targets": targets},
            config,
        )

    assert set(out["investigations"]) == set(deps)
    assert peak <= 6, f"expected cap 6, saw {peak}"
    assert peak > 1


@pytest.mark.asyncio
async def test_investigate_node_no_targets_short_circuits():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=MagicMock())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}
    from src.main_graph.subgraphs.remediation.investigate import (
        investigate_node,
    )

    out = await investigate_node(
        {"job_id": "j", "prep_result_id": "p", "targets": {}}, config
    )
    assert out == {"investigations": {}}
