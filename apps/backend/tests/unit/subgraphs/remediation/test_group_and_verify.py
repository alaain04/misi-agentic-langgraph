from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.nodes import group_and_verify as gv_module
from src.main_graph.subgraphs.remediation.nodes.group_and_verify import (
    group_and_verify_gate,
    route_after_group_verify,
)
from src.models.remediation import MigrationPlan, Remediation, VerificationResult
from src.models.results import PrepResult


def _prep(**overrides):
    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={},
        manifest_files=["package.json"],
        package_manager="npm",
        docker_image="node:lts-alpine",
        dependency_graph={"direct": {"lodash": "^4.17.11"}, "packages": {}},
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


@pytest.mark.asyncio
async def test_group_and_verify_gate_marks_group_fixed_on_green_verification():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "skipped",
            }
        },
        "requires_edges": {},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(
            return_value=(
                VerificationResult(installed=True, finding_resolved=True),
                None,
            )
        ),
    ):
        result = await group_and_verify_gate(state, config)

    assert result["remediations"]["lodash"]["status"] == "fixed"
    assert result.get("retry_targets") == []


@pytest.mark.asyncio
async def test_group_and_verify_gate_requests_retry_under_cap():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "skipped",
            }
        },
        "requires_edges": {},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(
            return_value=(VerificationResult(installed=True, tested=False), None)
        ),
    ):
        result = await group_and_verify_gate(state, config)

    assert result["retry_targets"] == ["lodash"]
    assert result["correction_rounds"] == 1
    assert "lodash" not in {
        k: v for k, v in result["remediations"].items() if v["status"] == "fixed"
    }


@pytest.mark.asyncio
async def test_group_and_verify_gate_keeps_workdir_when_consent_and_git_pr_configured():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    git_pr = MagicMock()
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": git_pr,
        }
    }

    state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "skipped",
            }
        },
        "requires_edges": {},
        "correction_rounds": 0,
    }
    mock_replay = AsyncMock(
        return_value=(
            VerificationResult(installed=True, finding_resolved=True),
            "/tmp/kept/repo",
        )
    )
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        mock_replay,
    ):
        result = await group_and_verify_gate(state, config)

    assert mock_replay.await_args.kwargs["keep_workdir"] is True
    assert result["verified_workdirs"] == {"lodash": "/tmp/kept/repo"}
    assert result["remediations"]["lodash"]["status"] == "fixed"


@pytest.mark.asyncio
async def test_group_and_verify_gate_does_not_request_keep_workdir_without_consent():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": False,
            "git_pr": None,
        }
    }

    state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "skipped",
            }
        },
        "requires_edges": {},
        "correction_rounds": 0,
    }
    mock_replay = AsyncMock(
        return_value=(
            VerificationResult(installed=True, finding_resolved=True),
            None,
        )
    )
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        mock_replay,
    ):
        result = await group_and_verify_gate(state, config)

    assert mock_replay.await_args.kwargs["keep_workdir"] is False
    assert result["verified_workdirs"] == {}


@pytest.mark.asyncio
async def test_group_and_verify_gate_deletes_kept_workdir_when_verification_failed():
    """replay_and_verify_group's actual (Task 1) contract: `keep` is decided
    right after apply_group_changes succeeds, BEFORE the verification result
    is inspected for greenness -- so a non-None work_dir comes back whenever
    keep_workdir=True and apply succeeded, even if verification then fails.
    group_and_verify_gate is the only place that sees this work_dir, so it
    alone is responsible for deleting it when the group did not verify
    green; nobody else will. Use a real mkdtemp'd .../repo dir (same shape
    copy_repo produces, per test_replay.py/test_pr_and_persist.py) so
    the assertion proves an actual shutil.rmtree happened, not just that the
    mock was called with the right args."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": MagicMock(),
        }
    }

    state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "skipped",
            }
        },
        "requires_edges": {},
        "correction_rounds": gv_module._MAX_CORRECTION_ROUNDS,
    }

    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    mock_replay = AsyncMock(
        return_value=(VerificationResult(installed=True, tested=False), work_dir)
    )
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        mock_replay,
    ):
        result = await group_and_verify_gate(state, config)

    assert result["verified_workdirs"] == {}
    assert result["remediations"]["lodash"]["status"] == "failed"
    # Proves group_and_verify_gate actually deleted the kept work_dir via
    # shutil.rmtree(os.path.dirname(work_dir)), not just that it declined
    # to record it in verified_workdirs.
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_group_and_verify_gate_deletes_prior_round_kept_workdir_when_superseded():
    """Regression: `connected_groups` builds its groups from `targets` UNION
    every name in `requires_edges`. On a retry round `targets` is narrowed by
    _resolve_working_targets to just the retry deps, but `requires_edges`
    accumulates across rounds via _merge_replace -- so an already-settled
    coupled group from round 0 is still re-derived (and re-verified) on round
    1 even though remediate_targets_node never re-dispatched it. With
    keep_workdir=True that mints a SECOND kept copy for the same deps while
    the round-0 copy stays on disk forever (nothing else ever sweeps it).
    The gate must delete any prior kept path it superseded or invalidated for
    a dep it settled this round. Uses real mkdtemp'd .../repo dirs so the
    assertions prove actual filesystem state, not just mock bookkeeping."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": MagicMock(),
        }
    }

    round0_root = tempfile.mkdtemp(prefix="test-remediation-r0-")
    round0_dir = os.path.join(round0_root, "repo")
    os.makedirs(round0_dir)
    round1_root = tempfile.mkdtemp(prefix="test-remediation-r1-")
    round1_dir = os.path.join(round1_root, "repo")
    os.makedirs(round1_dir)
    lodash_root = tempfile.mkdtemp(prefix="test-remediation-lodash-")
    lodash_dir = os.path.join(lodash_root, "repo")
    os.makedirs(lodash_dir)

    def _rem(dep, status="skipped"):
        return {
            "id": f"r-{dep}",
            "addresses": [dep],
            "target_dep": dep,
            "strategy": "bump",
            "to_range": "^1.0.0",
            "status": status,
        }

    green = VerificationResult(installed=True, finding_resolved=True)

    # Round 0: the coupled eslint group verifies green and its copy is kept.
    round0_state = {
        "prep_result_id": "prep-1",
        "targets": {"eslint": {}, "eslint-plugin-react": {}},
        "remediations": {
            "eslint": _rem("eslint"),
            "eslint-plugin-react": _rem("eslint-plugin-react"),
        },
        "requires_edges": {"eslint": ["eslint-plugin-react"]},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(return_value=(green, round0_dir)),
    ):
        round0_result = await group_and_verify_gate(round0_state, config)

    assert round0_result["verified_workdirs"] == {
        "eslint": round0_dir,
        "eslint-plugin-react": round0_dir,
    }
    assert os.path.exists(round0_dir)

    # Round 1: `targets` narrowed to the unrelated dep forcing the retry, but
    # requires_edges (and the round-0 remediations + verified_workdirs) are
    # still carried over by _merge_replace.
    round1_state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "eslint": _rem("eslint", status="fixed"),
            "eslint-plugin-react": _rem("eslint-plugin-react", status="fixed"),
            "lodash": _rem("lodash"),
        },
        "requires_edges": {"eslint": ["eslint-plugin-react"]},
        "correction_rounds": 1,
        "verified_workdirs": dict(round0_result["verified_workdirs"]),
    }
    mock_replay = AsyncMock(
        side_effect=[(green, round1_dir), (green, lodash_dir)],
    )
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        mock_replay,
    ):
        round1_result = await group_and_verify_gate(round1_state, config)

    # The already-settled eslint group really was re-verified this round --
    # that is the bug's mechanism, reproduced.
    assert mock_replay.await_count == 2
    # The superseded round-0 copy is gone from disk, and only the new path is
    # referenced.
    assert not os.path.exists(round0_root)
    assert round1_result["verified_workdirs"] == {
        "eslint": round1_dir,
        "eslint-plugin-react": round1_dir,
        "lodash": lodash_dir,
    }
    assert os.path.exists(round1_dir)

    shutil.rmtree(round1_root, ignore_errors=True)
    shutil.rmtree(lodash_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_group_and_verify_gate_deletes_prior_kept_workdir_when_reverify_fails():
    """The other half of the same bug: when the (unnecessary) re-verification
    of an already-settled group goes red, the gate deletes the copy it just
    made -- but this round's local verified_workdirs has no entry for those
    deps at all, so _merge_replace would preserve the round-0 path, leaving
    state pointing at a directory whose group is no longer `fixed`. The prior
    path must be deleted too."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": MagicMock(),
        }
    }

    prior_root = tempfile.mkdtemp(prefix="test-remediation-prior-")
    prior_dir = os.path.join(prior_root, "repo")
    os.makedirs(prior_dir)
    new_root = tempfile.mkdtemp(prefix="test-remediation-new-")
    new_dir = os.path.join(new_root, "repo")
    os.makedirs(new_dir)

    state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "fixed",
            }
        },
        "requires_edges": {},
        "correction_rounds": gv_module._MAX_CORRECTION_ROUNDS,
        "verified_workdirs": {"lodash": prior_dir},
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(
            return_value=(VerificationResult(installed=True, tested=False), new_dir)
        ),
    ):
        result = await group_and_verify_gate(state, config)

    assert result["verified_workdirs"] == {}
    assert result["remediations"]["lodash"]["status"] == "failed"
    assert not os.path.exists(new_root)
    assert not os.path.exists(prior_root)


@pytest.mark.asyncio
async def test_group_and_verify_gate_routes_never_dispatched_companion_to_retry():
    """A companion named only via `requires` (never independently selected
    by select_remediation_targets, never dispatched) has NO Remediation
    record at all yet -- this is not the same as dispatched-and-failed.
    While correction_rounds is under the cap, group_and_verify_gate must
    route the missing member into retry_targets instead of immediately
    failing the whole group. The already-dispatched sibling must be left
    untouched: not marked failed, not settled this round (the outer state's
    _merge_replace reducer preserves it across rounds since this round's
    return doesn't mention it)."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"eslint": {}},
        "remediations": {
            "eslint": {
                "id": "r1",
                "addresses": ["eslint"],
                "target_dep": "eslint",
                "strategy": "bump_with_codemod",
                "to_range": "^9.0.0",
                "status": "skipped",
            }
        },
        # eslint-plugin-react is named only via requires -- it was never
        # dispatched, so it has no entry in "remediations" above.
        "requires_edges": {"eslint": ["eslint-plugin-react"]},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
    ) as mock_replay:
        result = await group_and_verify_gate(state, config)

    mock_replay.assert_not_called()
    assert result["retry_targets"] == ["eslint-plugin-react"]
    assert result["correction_rounds"] == 1
    assert "eslint" not in result["remediations"]


@pytest.mark.asyncio
async def test_group_and_verify_gate_settles_group_once_companion_dispatched():
    """Once a previously-missing companion has been dispatched (simulating
    the round after remediate_targets_node's retry-mode branch redispatches
    it by the synthesized target name), the group has a Remediation record
    for every member and proceeds to normal replay/verify instead of staying
    stuck failing/retrying."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"eslint": {}, "eslint-plugin-react": {}},
        "remediations": {
            "eslint": {
                "id": "r1",
                "addresses": ["eslint"],
                "target_dep": "eslint",
                "strategy": "bump_with_codemod",
                "to_range": "^9.0.0",
                "status": "skipped",
            },
            "eslint-plugin-react": {
                "id": "r2",
                "addresses": [],
                "target_dep": "eslint-plugin-react",
                "strategy": "bump",
                "to_range": "^8.0.0",
                "status": "skipped",
            },
        },
        "requires_edges": {"eslint": ["eslint-plugin-react"]},
        "correction_rounds": 1,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(
            return_value=(
                VerificationResult(installed=True, finding_resolved=True),
                None,
            )
        ),
    ):
        result = await group_and_verify_gate(state, config)

    assert result["remediations"]["eslint"]["status"] == "fixed"
    assert result["remediations"]["eslint-plugin-react"]["status"] == "fixed"
    assert result.get("retry_targets") == []


@pytest.mark.asyncio
async def test_group_and_verify_gate_defers_whole_group_when_member_needs_migration():
    """A group containing an r3 (replace) member -- whether pre-classified
    by classify_targets_node or discovered mid-investigation -- must be
    deferred wholesale: no verification attempted, every member (including
    ones that would otherwise be green) settled as skipped."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"eslint": {}},
        "remediations": {
            "eslint": {
                "id": "r1",
                "addresses": ["eslint"],
                "target_dep": "eslint",
                "strategy": "bump_with_codemod",
                "to_range": "^9.0.0",
                "status": "skipped",
            },
            "eslint-plugin-react": {
                "id": "r2",
                "addresses": [],
                "target_dep": "eslint-plugin-react",
                "strategy": "replace",
                "status": "skipped",
                "skip_reason": "dependency migration - deferred, not yet supported",
            },
        },
        "requires_edges": {"eslint": ["eslint-plugin-react"]},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
    ) as mock_replay:
        result = await group_and_verify_gate(state, config)

    mock_replay.assert_not_called()
    assert result["remediations"]["eslint"]["status"] == "skipped"
    assert result["remediations"]["eslint"]["skip_reason"] == (
        "coupled to a dependency migration (r3) target - deferred"
    )
    assert result["remediations"]["eslint-plugin-react"]["status"] == "skipped"
    # The r3 member keeps its OWN reason -- that is where its replacement
    # proposal is carried. Only its coupled siblings get the group reason.
    assert result["remediations"]["eslint-plugin-react"]["skip_reason"] == (
        "dependency migration - deferred, not yet supported"
    )
    assert result.get("retry_targets") == []


@pytest.mark.asyncio
async def test_group_verify_preserves_plan_field():
    plan = MigrationPlan(target_dep="lodash", tier_hint="r1", tasks=[])
    rem = Remediation(
        addresses=["lodash"],
        target_dep="lodash",
        strategy="bump",
        to_range="^4.17.21",
        plan=plan,
    ).model_dump()

    prep = MagicMock(
        repo_path="/tmp/repo", docker_image="img", package_manager="npm"
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    green = VerificationResult(
        installed=True, built=True, tested=True, finding_resolved=True
    )
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(return_value=(green, None)),
    ):
        out = await group_and_verify_gate(
            {
                "prep_result_id": "p",
                "remediations": {"lodash": rem},
                "requires_edges": {},
                "targets": {"lodash": {}},
            },
            config,
        )
    assert out["remediations"]["lodash"]["status"] == "fixed"
    assert out["remediations"]["lodash"]["plan"]["target_dep"] == "lodash"


@pytest.mark.asyncio
async def test_group_and_verify_gate_fails_group_when_still_undispatched_at_cap():
    """Terminal case: correction_rounds has genuinely reached
    _MAX_CORRECTION_ROUNDS and a group member is STILL missing its
    Remediation record entirely (never dispatched even after every retry
    round). Only then does the group fail outright, with the existing
    fail-the-whole-group behavior."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"eslint": {}},
        "remediations": {
            "eslint": {
                "id": "r1",
                "addresses": ["eslint"],
                "target_dep": "eslint",
                "strategy": "bump_with_codemod",
                "to_range": "^9.0.0",
                "status": "skipped",
            }
        },
        "requires_edges": {"eslint": ["eslint-plugin-react"]},
        "correction_rounds": gv_module._MAX_CORRECTION_ROUNDS,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
    ) as mock_replay:
        result = await group_and_verify_gate(state, config)

    mock_replay.assert_not_called()
    assert result["remediations"]["eslint"]["status"] == "failed"
    assert (
        result["remediations"]["eslint"]["skip_reason"]
        == "a sibling dependency in this group was never dispatched"
    )
    assert result["remediations"]["eslint"]["required_by"] == []
    assert result.get("retry_targets") == []


@pytest.mark.asyncio
async def test_group_and_verify_gate_populates_required_by_on_cap_exceeded_skip():
    """A target pushed past the _MAX_GROUPS cap must still ship with
    required_by populated from requires_edges, not silently empty."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    # 20 unconnected single-member filler groups sort before the "za"/"zb"
    # group alphabetically, pushing {za, zb} to be the 21st group -- past
    # the _MAX_GROUPS=20 cap, into the overflow loop.
    filler_names = [f"filler{i}" for i in range(20)]
    targets = {name: {} for name in [*filler_names, "za", "zb"]}

    def _remediation(dep):
        return {
            "id": f"r-{dep}",
            "addresses": [dep],
            "target_dep": dep,
            "strategy": "bump",
            "to_range": "^1.0.0",
            "status": "skipped",
        }

    state = {
        "prep_result_id": "prep-1",
        "targets": targets,
        "remediations": {"za": _remediation("za"), "zb": _remediation("zb")},
        "requires_edges": {"za": ["zb"]},
        "correction_rounds": 0,
    }

    result = await group_and_verify_gate(state, config)

    assert result["remediations"]["zb"]["status"] == "skipped"
    assert result["remediations"]["zb"]["skip_reason"] == "target/group cap exceeded"
    assert result["remediations"]["zb"]["required_by"] == ["za"]
    assert result["remediations"]["za"]["required_by"] == []


def test_route_after_group_verify_retries_then_finishes():
    assert (
        route_after_group_verify({"retry_targets": ["lodash"]})
        == "remediate_targets_node"
    )
    assert route_after_group_verify({"retry_targets": []}) == "pr_and_persist_node"
    assert route_after_group_verify({}) == "pr_and_persist_node"


@pytest.mark.asyncio
async def test_group_and_verify_gate_never_marks_noop_bump_group_fixed():
    """A no-op bump group has nothing to apply, so replaying it verifies a
    pristine copy and comes back green. It must not be reported as fixed."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"stale-dep": {}},
        "remediations": {
            "stale-dep": {
                "id": "r1",
                "addresses": ["stale-dep"],
                "target_dep": "stale-dep",
                "strategy": "bump",
                "from_range": "1.2.3",
                "to_range": None,
                "status": "skipped",
                "plan": {
                    "target_dep": "stale-dep",
                    "tier_hint": "r1",
                    "tasks": [{"kind": "bump", "rationale": "x", "to_range": "1.2.3"}],
                    "requires": [],
                },
            }
        },
        "requires_edges": {},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(
            return_value=(
                VerificationResult(installed=True, finding_resolved=True),
                None,
            )
        ),
    ) as mock_replay:
        result = await group_and_verify_gate(state, config)

    mock_replay.assert_not_called()
    rem = result["remediations"]["stale-dep"]
    assert rem["status"] == "skipped"
    assert "no upgrade available" in rem["skip_reason"]


@pytest.mark.asyncio
async def test_group_and_verify_gate_preserves_replacement_proposal_reason():
    """The gate's coupled-group branch overwrote every member's skip_reason,
    erasing the r3 member's proposal. Only its siblings get the generic
    group reason now."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"matcha": {}, "sibling": {}},
        "remediations": {
            "matcha": {
                "id": "r1",
                "addresses": ["matcha"],
                "target_dep": "matcha",
                "strategy": "replace",
                "replacement_dep": "tinybench",
                "status": "skipped",
                "skip_reason": "replacement proposed: tinybench@^2.5.0 -- review",
            },
            "sibling": {
                "id": "r2",
                "addresses": ["sibling"],
                "target_dep": "sibling",
                "strategy": "bump",
                "to_range": "^2.0.0",
                "status": "skipped",
            },
        },
        "requires_edges": {"matcha": ["sibling"]},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.nodes.group_and_verify.replay_and_verify_group",
        AsyncMock(return_value=(VerificationResult(installed=True), None)),
    ) as mock_replay:
        result = await group_and_verify_gate(state, config)

    mock_replay.assert_not_called()
    assert "tinybench" in result["remediations"]["matcha"]["skip_reason"]
    assert "coupled" in result["remediations"]["sibling"]["skip_reason"]
