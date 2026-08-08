from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.errors import GraphRecursionError

from src.main_graph.subgraphs.remediation.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    _assemble_remediations,
    _resolve_working_targets,
    group_and_verify_gate,
    pr_and_persist_node,
    remediate_targets_node,
    route_after_group_verify,
)
from src.models.remediation import (
    FindingSummary,
    MigrationPlan,
    Remediation,
    RemediationOutcome,
    RemediationTarget,
    VerificationResult,
)
from src.models.results import PrepResult


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


def _agent_returning(outcomes: dict[str, dict]):
    return MagicMock(ainvoke=AsyncMock(return_value={"outcomes": outcomes}))


def test_resolve_working_targets_retry_synthesizes_empty_finding_summaries():
    prep = _prep(dependency_graph={"direct": {"lodash": "^4.17.11"}, "packages": {}})
    state = {"retry_targets": ["lodash"], "targets": {}}
    out = _resolve_working_targets(state, prep)
    assert out["lodash"]["finding_summaries"] == []


def test_assemble_remediations_carries_finding_summaries_through_no_plan_branch():
    targets = {
        "lodash": RemediationTarget(
            target_dep="lodash",
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash", severity="high", description="proto pollution"
                )
            ],
        ).model_dump()
    }
    out = _assemble_remediations(targets, plans={}, outcomes={}, omit=set())
    assert out["lodash"]["finding_summaries"] == [
        {"dep_name": "lodash", "severity": "high", "description": "proto pollution"}
    ]


def test_assemble_remediations_carries_finding_summaries_through_outcome_branch():
    fs = FindingSummary(
        dep_name="lodash", severity="high", description="proto pollution"
    )
    targets = {
        "lodash": RemediationTarget(
            target_dep="lodash", addresses=["lodash"], finding_summaries=[fs]
        ).model_dump()
    }
    plans = {
        "lodash": MigrationPlan(target_dep="lodash", tier_hint="r1").model_dump()
    }
    outcomes = {"lodash": RemediationOutcome(to_range="^4.17.21").model_dump()}
    out = _assemble_remediations(targets, plans=plans, outcomes=outcomes, omit=set())
    assert out["lodash"]["finding_summaries"] == [fs.model_dump()]


@pytest.mark.asyncio
async def test_remediate_targets_node_produces_remediation_per_target():
    """A bump-only plan is now folded into the SAME execution agent as
    codemod (spec 2026-08-08-remediation-flatten-planning-execution, D2/D3)
    -- it must still produce a commit_outcome, not a bare data-carry."""
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.15"}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    targets = {
        "lodash": RemediationTarget(
            target_dep="lodash",
            addresses=["lodash"],
            current_range="^4.17.15",
            tier="r1",
        ).model_dump()
    }
    plans = {
        "lodash": {
            "target_dep": "lodash",
            "tier_hint": "r1",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "bump",
                    "rationale": "patch",
                    "to_range": "^4.17.21",
                    "files": [],
                    "replacement_dep": None,
                    "replacement_range": None,
                }
            ],
            "requires": [],
        }
    }
    outcome = RemediationOutcome(strategy="bump", to_range="^4.17.21").model_dump()

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent",
            return_value=_agent_returning({"lodash": outcome}),
        ),
    ):
        out = await remediate_targets_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
                "migration_plans": plans,
            },
            config,
        )

    assert out["migration_plans"]["lodash"]["tier_hint"] == "r1"
    rem = out["remediations"]["lodash"]
    assert rem["target_dep"] == "lodash"
    assert rem["to_range"] == "^4.17.21"
    assert rem["plan"]["tasks"][0]["kind"] == "bump"


@pytest.mark.asyncio
async def test_remediate_targets_node_derives_requires_edges_from_plan():
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        detected_package_manager="npm",
        dependency_graph={"direct": {"eslint": "^8.0.0"}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    targets = {
        "eslint": RemediationTarget(
            target_dep="eslint",
            addresses=["eslint"],
            current_range="^8.0.0",
            tier="r2",
        ).model_dump()
    }
    plans = {
        "eslint": {
            "target_dep": "eslint",
            "tier_hint": "r2",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "bump",
                    "rationale": "companion coupling",
                    "to_range": "^9.0.0",
                    "files": [],
                    "replacement_dep": None,
                    "replacement_range": None,
                }
            ],
            # companion-dep is NOT an original target -- only named here.
            "requires": ["companion-dep"],
        }
    }
    outcome = RemediationOutcome(strategy="bump", to_range="^9.0.0").model_dump()

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent",
            return_value=_agent_returning({"eslint": outcome}),
        ) as mock_build_agent,
    ):
        out = await remediate_targets_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
                "migration_plans": plans,
            },
            config,
        )

    assert out["requires_edges"] == {"eslint": ["companion-dep"]}
    # companion-dep has no plan of its own yet (never independently
    # selected) -- it must NOT be dispatched to the execution agent this
    # round; group_and_verify_gate's own grouping catches it via the
    # missing-member retry path instead. Only one group (["eslint"]) exists
    # to dispatch, so the agent is built exactly once.
    mock_build_agent.assert_called_once()


@pytest.mark.asyncio
async def test_remediate_targets_node_codemod_outcome_becomes_patch_remediation():
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.15"}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    targets = {
        "lodash": RemediationTarget(
            target_dep="lodash",
            addresses=["lodash"],
            current_range="^4.17.15",
            tier="r2",
        ).model_dump()
    }
    plans = {
        "lodash": {
            "target_dep": "lodash",
            "tier_hint": "r2",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "codemod",
                    "rationale": "breaking change",
                    "to_range": "^5.0.0",
                    "files": [],
                    "replacement_dep": None,
                    "replacement_range": None,
                }
            ],
            "requires": [],
        }
    }
    outcome = RemediationOutcome(
        strategy="bump_with_codemod",
        to_range="^5.0.0",
        code_diff="DIFF",
        status="skipped",
    ).model_dump()

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent",
            return_value=_agent_returning({"lodash": outcome}),
        ),
    ):
        out = await remediate_targets_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
                "migration_plans": plans,
            },
            config,
        )

    rem = out["remediations"]["lodash"]
    assert rem["patch"] == "DIFF"
    assert rem["to_range"] == "^5.0.0"
    assert rem["strategy"] == "bump_with_codemod"
    assert rem["plan"]["tasks"][0]["kind"] == "codemod"


@pytest.mark.asyncio
async def test_remediate_targets_node_replace_plan_settles_deferred_without_dispatch():
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        detected_package_manager="npm",
        dependency_graph={"direct": {"old-dep": "1.0.0"}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    targets = {
        "old-dep": RemediationTarget(
            target_dep="old-dep",
            addresses=["old-dep"],
            current_range="^1.0.0",
            tier="r3",
        ).model_dump()
    }
    plans = {
        "old-dep": {
            "target_dep": "old-dep",
            "tier_hint": "r3",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "replace",
                    "rationale": "unmaintained",
                    "to_range": None,
                    "files": [],
                    "replacement_dep": "new-dep",
                    "replacement_range": "^1.0.0",
                }
            ],
            "requires": [],
        }
    }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent"
        ) as mock_build_agent,
    ):
        out = await remediate_targets_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
                "migration_plans": plans,
            },
            config,
        )

    mock_build_agent.assert_not_called()  # replace is never dispatched to an agent
    rem = out["remediations"]["old-dep"]
    assert rem["strategy"] == "replace"
    assert rem["status"] == "skipped"
    assert "deferred" in rem["skip_reason"]
    assert rem["plan"]["tasks"][0]["kind"] == "replace"


@pytest.mark.asyncio
async def test_remediate_targets_node_no_outcome_fails_honestly():
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.15"}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    targets = {
        "lodash": RemediationTarget(
            target_dep="lodash",
            addresses=["lodash"],
            current_range="^4.17.15",
            tier="r2",
        ).model_dump()
    }
    plans = {
        "lodash": {
            "target_dep": "lodash",
            "tier_hint": "r2",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "codemod",
                    "rationale": "breaking change",
                    "to_range": "^5.0.0",
                    "files": [],
                    "replacement_dep": None,
                    "replacement_range": None,
                }
            ],
            "requires": [],
        }
    }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent",
            return_value=_agent_returning({}),
        ),
    ):
        out = await remediate_targets_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
                "migration_plans": plans,
            },
            config,
        )

    rem = out["remediations"]["lodash"]
    assert rem["status"] == "failed"
    assert "produced no outcome" in rem["skip_reason"]


@pytest.mark.asyncio
async def test_remediate_targets_node_no_targets_short_circuits():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    result = await remediate_targets_node(
        {
            "job_id": "job-1",
            "prep_result_id": "prep-1",
            "analysis_result_id": "a-1",
            "concern": "c",
            "targets": {},
        },
        config,
    )
    assert result == {
        "targets": {},
        "remediations": {},
        "requires_edges": {},
        "migration_plans": {},
    }


@pytest.mark.asyncio
async def test_remediate_targets_node_retry_synthesizes_unknown_companion_target():
    """A companion target discovered mid-run purely via another target's
    `requires` never gets added to state["targets"] anywhere. If
    group_and_verify_gate puts such a name into retry_targets, the retry
    round must synthesize a real target entry for it (unchanged
    _resolve_working_targets behavior) and plan+dispatch it, not silently
    drop it."""
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "^4.17.11", "companion-dep": "^2.0.0"},
            "packages": {},
        }
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "analysis_result_id": "a-1",
        "concern": "c",
        "retry_targets": ["lodash", "companion-dep"],
        "targets": {
            "lodash": {
                "target_dep": "lodash",
                "addresses": ["lodash"],
                "current_range": "^4.17.11",
            }
        },
        # No migration_plans in state -- both retry targets are "unplanned"
        # this round and must go through the single-target planning fallback.
    }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_plans_for_targets",
            AsyncMock(return_value={}),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent"
        ) as mock_build_agent,
    ):
        result = await remediate_targets_node(state, config)

    mock_build_agent.assert_not_called()  # no plan for either -> nothing to dispatch
    assert set(result["targets"]) == {"lodash", "companion-dep"}
    assert result["targets"]["companion-dep"] == {
        "target_dep": "companion-dep",
        "addresses": [],
        "finding_summaries": [],
        "current_range": "^2.0.0",
        "tier": None,
    }
    # Known target keeps its real addresses/current_range untouched.
    assert result["targets"]["lodash"]["addresses"] == ["lodash"]
    # No plan for either this round -> both fail honestly, not silently dropped.
    assert result["remediations"]["lodash"]["status"] == "failed"
    assert result["remediations"]["companion-dep"]["status"] == "failed"


@pytest.mark.asyncio
async def test_remediate_targets_node_recursion_limit_omits_only_that_group():
    """Spec D9/D10: bounds must fail honestly instead of crashing the job.
    A real GraphRecursionError from one group's execution agent must not
    propagate, and -- unlike the old single-agent design, which had to wipe
    every target's remediation because planning and execution were the same
    aborted call -- only THAT group's targets are omitted from
    `remediations` (routing them through group_and_verify_gate's existing
    missing-member retry path). Planning already happened separately and is
    unaffected."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    plans = {
        "lodash": {
            "target_dep": "lodash",
            "tier_hint": "r1",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "bump",
                    "rationale": "patch",
                    "to_range": "^4.17.21",
                    "files": [],
                    "replacement_dep": None,
                    "replacement_range": None,
                }
            ],
            "requires": [],
        }
    }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent",
            return_value=MagicMock(
                ainvoke=AsyncMock(side_effect=GraphRecursionError("limit"))
            ),
        ),
    ):
        result = await remediate_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {
                    "lodash": {
                        "target_dep": "lodash",
                        "addresses": ["lodash"],
                        "current_range": "^4.17.11",
                    }
                },
                "migration_plans": plans,
            },
            config,
        )

    assert result["remediations"] == {}
    assert "lodash" in result["targets"]
    # The plan itself survives -- only execution was aborted, not planning.
    assert result["migration_plans"]["lodash"]["tier_hint"] == "r1"


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
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
    copy_repo produces, per test_replay.py/test_pr_and_persist_node_*) so
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
        "correction_rounds": deepagent_nodes._MAX_CORRECTION_ROUNDS,
    }

    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    mock_replay = AsyncMock(
        return_value=(VerificationResult(installed=True, tested=False), work_dir)
    )
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
    ) as mock_replay:
        result = await group_and_verify_gate(state, config)

    mock_replay.assert_not_called()
    assert result["remediations"]["eslint"]["status"] == "skipped"
    assert result["remediations"]["eslint"]["skip_reason"] == (
        "coupled to a dependency migration (r3) target - deferred"
    )
    assert result["remediations"]["eslint-plugin-react"]["status"] == "skipped"
    assert result["remediations"]["eslint-plugin-react"]["skip_reason"] == (
        "coupled to a dependency migration (r3) target - deferred"
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
        repo_path="/tmp/repo", docker_image="img", detected_package_manager="npm"
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    green = VerificationResult(
        installed=True, built=True, tested=True, finding_resolved=True
    )
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
        "correction_rounds": deepagent_nodes._MAX_CORRECTION_ROUNDS,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
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
async def test_pr_and_persist_node_skips_pr_when_consent_false():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": False,
            "git_pr": git_pr,
        }
    }

    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
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
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_not_called()
    assert result == {"remediation_result_id": "rid-1"}


@pytest.mark.asyncio
async def test_pr_and_persist_node_opens_one_pr_when_consent_true():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep(repo_path="/original/repo"))
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    git_pr.open_pr = AsyncMock(return_value="https://gh/pr/1")
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": git_pr,
        }
    }

    # A real dst/repo-shaped temp dir matching copy_repo's actual contract
    # -- pr_and_persist_node cleans up via
    # shutil.rmtree(os.path.dirname(work_dir)), so a test double shaped any
    # other way (e.g. a bare tmp_path, not tmp_path/repo) would make that
    # cleanup target something far too broad, like a shared pytest tmp root.
    # This dir is now pre-verified by group_and_verify_gate (Task 2) --
    # pr_and_persist_node must not touch its contents, only ship it.
    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "lodash": {
                "id": "r1",
                "addresses": ["lodash"],
                "target_dep": "lodash",
                "strategy": "bump",
                "to_range": "^4.17.21",
                "status": "fixed",
                "verification": {"installed": True, "finding_resolved": True},
            }
        },
        "verified_workdirs": {"lodash": work_dir},
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_awaited_once()
    branch = git_pr.open_pr.await_args.args[1]
    assert branch == "remediation/job-1-lodash"
    assert result == {"remediation_result_id": "rid-1"}
    # Cleanup must target the mkdtemp root, not something broader.
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_pr_and_persist_node_groups_shared_workdir_into_one_pr():
    """Two deps whose verified_workdirs entries point at the SAME path (a
    coupled group group_and_verify_gate already verified together) must
    ship as one PR, not two."""
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep(repo_path="/original/repo"))
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    git_pr.open_pr = AsyncMock(return_value="https://gh/pr/1")
    config = {
        "configurable": {
            "result_dao": dao,
            "container": MagicMock(),
            "remediate": True,
            "git_pr": git_pr,
        }
    }

    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)

    verification = {"installed": True, "finding_resolved": True}
    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "eslint": {
                "id": "r1",
                "addresses": ["eslint"],
                "target_dep": "eslint",
                "strategy": "bump",
                "to_range": "^9.0.0",
                "status": "fixed",
                "verification": verification,
            },
            "eslint-plugin-react": {
                "id": "r2",
                "addresses": [],
                "target_dep": "eslint-plugin-react",
                "strategy": "bump",
                "to_range": "^8.0.0",
                "status": "fixed",
                "verification": verification,
            },
        },
        "verified_workdirs": {
            "eslint": work_dir,
            "eslint-plugin-react": work_dir,
        },
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_awaited_once()
    assert result == {"remediation_result_id": "rid-1"}
    remediation = dao.save_remediation.await_args.args[0]
    by_dep = {r.target_dep: r for r in remediation.remediations}
    assert by_dep["eslint"].pr_url == "https://gh/pr/1"
    assert by_dep["eslint-plugin-react"].pr_url == "https://gh/pr/1"
    assert by_dep["eslint"].branch == by_dep["eslint-plugin-react"].branch


def test_pr_title_and_body_bump_case():
    members = [
        Remediation(
            id="r1",
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash",
                    severity="high",
                    description="known prototype pollution vulnerability",
                )
            ],
            target_dep="lodash",
            strategy="bump",
            from_range="^4.17.11",
            to_range="^4.17.21",
            status="fixed",
        )
    ]
    verification = VerificationResult(
        installed=True, tested=True, finding_resolved=True
    )

    title, body = deepagent_nodes._pr_title_and_body(members, verification)

    assert "Automated dependency remediation" not in body
    assert body.startswith("## Summary")
    assert title == "Remediate lodash (bump)"
    assert "please review before merging" not in body
    assert "| lodash | bump | `^4.17.11` -> `^4.17.21` | - |" in body
    assert (
        "| lodash | high | known prototype pollution vulnerability | lodash |"
        in body
    )
    assert "- [x] Install" in body
    assert "- [x] Tests" in body
    assert "- [x] Audit re-scan: finding no longer present" in body
    assert "## Migration notes" not in body


def test_pr_findings_table_truncates_long_description():
    long_desc = "x" * 300
    members = [
        Remediation(
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash", severity="high", description=long_desc
                )
            ],
            target_dep="lodash",
        )
    ]
    table = deepagent_nodes._pr_findings_table(members)
    row = next(line for line in table.splitlines() if line.startswith("| lodash"))
    cell = row.split(" | ")[2]
    assert len(cell) <= 150
    assert cell.endswith("…")


def test_pr_findings_table_escapes_pipe_in_description():
    members = [
        Remediation(
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash",
                    severity="high",
                    description=(
                        "Prototype pollution | affects parse() | CVE-2021-44906"
                    ),
                )
            ],
            target_dep="lodash",
        )
    ]
    table = deepagent_nodes._pr_findings_table(members)
    row = next(line for line in table.splitlines() if line.startswith("| lodash"))
    # Exactly 4 cells: Finding, Severity, Description, Resolved by.
    assert row.count(" | ") == 3
    assert "\\|" in row


def test_pr_findings_table_none_when_no_addresses():
    # `_pr_findings_table` falls back to `r.target_dep` when `addresses` is
    # empty (pre-existing behavior, unchanged by this task), so a member with
    # addresses=[] still yields a row. The "None." branch is only reachable
    # with an empty group.
    members: list[Remediation] = []
    assert deepagent_nodes._pr_findings_table(members) == "None."


def test_pr_findings_table_dash_when_no_summary_for_finding():
    members = [Remediation(addresses=["lodash"], target_dep="lodash")]
    table = deepagent_nodes._pr_findings_table(members)
    assert "| lodash | - | - | lodash |" in table


def test_pr_verification_summary_all_passed():
    v = VerificationResult(
        installed=True, built=True, tested=True, finding_resolved=True
    )
    summary = deepagent_nodes._pr_verification_summary(v)
    assert summary == (
        "- [x] Install\n"
        "- [x] Build\n"
        "- [x] Tests\n"
        "- [x] Audit re-scan: finding no longer present"
    )


def test_pr_verification_summary_failure_and_omitted_fields():
    v = VerificationResult(
        installed=True, built=None, tested=False, finding_resolved=None
    )
    summary = deepagent_nodes._pr_verification_summary(v)
    assert summary == "- [x] Install\n- [ ] Tests (failed)"


def test_pr_title_and_body_replace_case_includes_migration_notes():
    members = [
        Remediation(
            id="r1",
            addresses=["left-pad", "old-transitive"],
            target_dep="left-pad",
            strategy="replace",
            replacement_dep="pad-string",
            replacement_range="^2.0.0",
            migration_plan="swap default import for the named `pad` export",
            status="fixed",
        )
    ]
    verification = VerificationResult(installed=True, finding_resolved=False)

    title, body = deepagent_nodes._pr_title_and_body(members, verification)

    assert title == "Remediate left-pad (replace - review required)"
    assert "please review before merging" in body
    assert "replaced with `pad-string@^2.0.0`" in body
    assert "| left-pad | - | - | left-pad |" in body
    assert "| old-transitive | - | - | left-pad |" in body
    assert "- [ ] Audit re-scan (failed)" not in body
    assert "- [ ] Audit re-scan: finding still present" in body
    assert "## Migration notes" in body
    assert "swap default import for the named `pad` export" in body
