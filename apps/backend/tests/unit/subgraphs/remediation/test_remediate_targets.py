from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.errors import GraphRecursionError

from src.main_graph.subgraphs.remediation.nodes.remediate_targets import (
    _assemble_remediations,
    _resolve_working_targets,
    remediate_targets_node,
)
from src.models.remediation import (
    FindingSummary,
    MigrationPlan,
    RemediationOutcome,
    RemediationTarget,
)
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
    plans = {"lodash": MigrationPlan(target_dep="lodash", tier_hint="r1").model_dump()}
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
        package_manager="npm",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent",
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
        package_manager="npm",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent",
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
        package_manager="npm",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent",
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
        package_manager="npm",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent"
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
    assert "replacement proposed: new-dep@^1.0.0" in rem["skip_reason"]
    assert rem["replacement_dep"] == "new-dep"
    assert rem["plan"]["tasks"][0]["kind"] == "replace"


@pytest.mark.asyncio
async def test_remediate_targets_node_no_outcome_fails_honestly():
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        package_manager="npm",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_plans_for_targets",
            AsyncMock(return_value={}),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent"
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
        "latest_version": None,
        "resolved_repo": None,
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent",
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
async def test_remediate_targets_node_group_failure_cancels_sibling_groups():
    """A raw, unrecoverable exception from one group's execution agent (e.g.
    a RateLimitError once its retry budget is exhausted) must both (a)
    propagate out of remediate_targets_node as itself, not wrapped in an
    ExceptionGroup -- job_runner stores str(exc) verbatim as the job's error
    -- and (b) cancel any other still-running group instead of leaving it as
    an untracked orphan that keeps calling the LLM after this node, and the
    job, have already failed."""

    def _plan(dep: str, to_range: str) -> dict:
        return {
            "target_dep": dep,
            "tier_hint": "r1",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "bump",
                    "rationale": "patch",
                    "to_range": to_range,
                    "files": [],
                    "replacement_dep": None,
                    "replacement_range": None,
                }
            ],
            "requires": [],
        }

    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "^4.17.11", "axios": "^1.0.0"},
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
        "targets": {
            "lodash": {
                "target_dep": "lodash",
                "addresses": ["lodash"],
                "current_range": "^4.17.11",
            },
            "axios": {
                "target_dep": "axios",
                "addresses": ["axios"],
                "current_range": "^1.0.0",
            },
        },
        "migration_plans": {
            "lodash": _plan("lodash", "^4.17.21"),
            "axios": _plan("axios", "^1.1.0"),
        },
    }

    sibling_cancelled = asyncio.Event()

    async def _slow_ainvoke(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise
        return {"outcomes": {}}

    def _build_agent(
        work_dir, container, docker_image, package_manager, resolved_repos=None
    ):
        if not sibling_cancelled.is_set() and _build_agent.calls == 0:
            _build_agent.calls += 1
            return MagicMock(
                ainvoke=AsyncMock(side_effect=RuntimeError("rate limit exhausted"))
            )
        return MagicMock(ainvoke=AsyncMock(side_effect=_slow_ainvoke))

    _build_agent.calls = 0

    with (
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent",
            side_effect=_build_agent,
        ),
    ):
        with pytest.raises(RuntimeError, match="rate limit exhausted"):
            await remediate_targets_node(state, config)

    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_remediate_targets_node_r3_tier_overrides_bump_only_plan():
    """Regression (job 6a7773a7576d0efd7796aa8c, `matcha`): classify tiered
    the target r3, but the planner emitted a bump-only plan whose to_range
    was the installed version. Routing keyed only off the plan's task kinds,
    so the target was dispatched and "bumped" 0.7.0 -> 0.7.0. The tier is
    binding now, so it never reaches an execution agent."""
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        package_manager="npm",
        dependency_graph={"direct": {"matcha": "0.7.0"}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    targets = {
        "matcha": RemediationTarget(
            target_dep="matcha",
            addresses=["matcha"],
            current_range="0.7.0",
            tier="r3",
        ).model_dump()
    }
    plans = {
        "matcha": {
            "target_dep": "matcha",
            "tier_hint": "r3",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "bump",
                    "rationale": "Clean upgrade with no migration needed.",
                    "to_range": "^0.7.0",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent"
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

    mock_build_agent.assert_not_called()
    rem = out["remediations"]["matcha"]
    assert rem["strategy"] == "replace"
    assert rem["status"] == "skipped"
    assert "no replacement candidate was identified" in rem["skip_reason"]


@pytest.mark.asyncio
async def test_remediate_targets_node_noop_bump_plan_settles_without_dispatch():
    """A bump to the range already declared changes nothing -- executing it
    burns a container verify cycle only to fail with a misleading reason."""
    prep = MagicMock(
        repo_path="/tmp/repo",
        docker_image="img",
        package_manager="npm",
        dependency_graph={"direct": {"stale-dep": "1.2.3"}, "packages": {}},
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    targets = {
        "stale-dep": RemediationTarget(
            target_dep="stale-dep",
            addresses=["stale-dep"],
            current_range="1.2.3",
            tier="r1",
        ).model_dump()
    }
    plans = {
        "stale-dep": {
            "target_dep": "stale-dep",
            "tier_hint": "r1",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "bump",
                    "rationale": "clean upgrade",
                    "to_range": "1.2.3",
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
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.nodes.remediate_targets.build_execution_agent"
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

    mock_build_agent.assert_not_called()
    rem = out["remediations"]["stale-dep"]
    assert rem["status"] == "skipped"
    assert "no upgrade available" in rem["skip_reason"]


def test_assemble_remediations_carries_replacement_proposal_onto_record():
    """The r3 record is the only place a replacement proposal is delivered
    -- automating the migration is still deferred, so the named candidate
    and its reasoning ARE the deliverable. They used to be dropped."""
    targets = {
        "matcha": RemediationTarget(
            target_dep="matcha",
            addresses=["matcha"],
            current_range="0.7.0",
            tier="r3",
        ).model_dump()
    }
    plans = {
        "matcha": {
            "target_dep": "matcha",
            "tier_hint": "r3",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "replace",
                    "rationale": "matcha is unmaintained; tinybench is active.",
                    "replacement_dep": "tinybench",
                    "replacement_range": "^2.5.0",
                }
            ],
            "requires": [],
        }
    }

    out = _assemble_remediations(targets, plans, outcomes={}, omit=set())

    rem = out["matcha"]
    assert rem["strategy"] == "replace"
    assert rem["replacement_dep"] == "tinybench"
    assert rem["replacement_range"] == "^2.5.0"
    assert "tinybench" in rem["migration_plan"]
    assert "replacement proposed: tinybench@^2.5.0" in rem["skip_reason"]


def test_assemble_remediations_says_so_when_no_replacement_candidate_named():
    targets = {
        "matcha": RemediationTarget(
            target_dep="matcha",
            addresses=["matcha"],
            current_range="0.7.0",
            tier="r3",
        ).model_dump()
    }
    plans = {
        "matcha": {
            "target_dep": "matcha",
            "tier_hint": "r3",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "replace",
                    "rationale": "No confident candidate for a benchmark lib.",
                    "replacement_dep": None,
                    "replacement_range": None,
                }
            ],
            "requires": [],
        }
    }

    out = _assemble_remediations(targets, plans, outcomes={}, omit=set())

    rem = out["matcha"]
    assert rem["replacement_dep"] is None
    assert "no replacement candidate was identified" in rem["skip_reason"]
