from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.errors import GraphRecursionError

from src.main_graph.subgraphs.remediation.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    group_and_verify_gate,
    pr_and_persist_node,
    root_deepagent_node,
    route_after_group_verify,
)
from src.models.remediation import (
    MigrationPlan,
    Remediation,
    RemediationOutcome,
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


@pytest.mark.asyncio
async def test_root_node_produces_plan_and_remediation_per_target():
    from src.models.remediation import RemediationTarget

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
    investigations = {
        "lodash": {
            "target_dep": "lodash",
            "dependents": [],
            "call_sites": [],
            "release": {
                "from_version": "4.17.15",
                "to_version": "4.17.21",
                "migration_needed": False,
                "migration_guide": "",
                "breaking_changes": [],
            },
        }
    }

    committed = {
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

    async def _fake_invoke(initial_state, run_config):
        return {
            "migration_plans": committed,
            "remediations": {},
            "requires_edges": {},
            "outcomes": {},
        }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes."
            "_build_planning_agent",
            return_value=MagicMock(ainvoke=AsyncMock(side_effect=_fake_invoke)),
        ),
    ):
        out = await root_deepagent_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": investigations,
            },
            config,
        )

    assert out["migration_plans"]["lodash"]["tier_hint"] == "r1"
    rem = out["remediations"]["lodash"]
    assert rem["target_dep"] == "lodash"
    assert rem["to_range"] == "^4.17.21"
    assert rem["plan"]["tasks"][0]["kind"] == "bump"


@pytest.mark.asyncio
async def test_root_node_derives_requires_edges_from_committed_plan():
    """The planning agent never writes a `requires_edges` state channel --
    only `commit_plan` writing `migration_plans`, where each MigrationPlan
    carries its own `requires` field. root_deepagent_node must derive
    requires_edges from that field itself (spec D7 companion coupling),
    not read a `requires_edges` key off the agent's result."""
    from src.models.remediation import RemediationTarget

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

    committed = {
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
            "requires": ["companion-dep"],
        }
    }

    async def _fake_invoke(initial_state, run_config):
        return {
            "migration_plans": committed,
            "remediations": {},
            "outcomes": {},
        }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes."
            "_build_planning_agent",
            return_value=MagicMock(ainvoke=AsyncMock(side_effect=_fake_invoke)),
        ),
    ):
        out = await root_deepagent_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
            },
            config,
        )

    assert out["requires_edges"] == {"eslint": ["companion-dep"]}


@pytest.mark.asyncio
async def test_root_node_codemod_outcome_becomes_patch_remediation():
    from src.models.remediation import RemediationTarget

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

    committed = {
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

    async def _fake_invoke(initial_state, run_config):
        return {
            "migration_plans": committed,
            "remediations": {},
            "requires_edges": {},
            "outcomes": {"lodash": outcome},
        }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes."
            "_build_planning_agent",
            return_value=MagicMock(ainvoke=AsyncMock(side_effect=_fake_invoke)),
        ),
    ):
        out = await root_deepagent_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
            },
            config,
        )

    rem = out["remediations"]["lodash"]
    assert rem["patch"] == "DIFF"
    assert rem["to_range"] == "^5.0.0"
    assert rem["strategy"] == "bump_with_codemod"
    assert rem["plan"]["tasks"][0]["kind"] == "codemod"


@pytest.mark.asyncio
async def test_root_node_replace_plan_settles_deferred():
    from src.models.remediation import RemediationTarget

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

    committed = {
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

    async def _fake_invoke(initial_state, run_config):
        return {
            "migration_plans": committed,
            "remediations": {},
            "requires_edges": {},
            "outcomes": {},
        }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes."
            "_build_planning_agent",
            return_value=MagicMock(ainvoke=AsyncMock(side_effect=_fake_invoke)),
        ),
    ):
        out = await root_deepagent_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
            },
            config,
        )

    rem = out["remediations"]["old-dep"]
    assert rem["strategy"] == "replace"
    assert rem["status"] == "skipped"
    assert "deferred" in rem["skip_reason"]
    assert rem["plan"]["tasks"][0]["kind"] == "replace"


@pytest.mark.asyncio
async def test_root_node_codemod_without_outcome_fails_honestly():
    from src.models.remediation import RemediationTarget

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

    committed = {
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

    async def _fake_invoke(initial_state, run_config):
        return {
            "migration_plans": committed,
            "remediations": {},
            "requires_edges": {},
            "outcomes": {},
        }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes."
            "_build_planning_agent",
            return_value=MagicMock(ainvoke=AsyncMock(side_effect=_fake_invoke)),
        ),
    ):
        out = await root_deepagent_node(
            {
                "job_id": "j",
                "prep_result_id": "p",
                "targets": targets,
                "investigations": {},
            },
            config,
        )

    rem = out["remediations"]["lodash"]
    assert rem["status"] == "failed"
    assert "codemod produced no outcome" in rem["skip_reason"]


@pytest.mark.asyncio
async def test_root_deepagent_node_no_targets_short_circuits():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    result = await root_deepagent_node(
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
async def test_root_deepagent_node_retry_synthesizes_unknown_companion_target():
    """A companion target discovered mid-run purely via a subagent's
    `requires` signal never gets added to state["targets"] anywhere
    (subagent_wrapper._run only returns remediations/requires_edges). If
    group_and_verify_gate puts such a name into retry_targets, the retry
    round must still explicitly redispatch it instead of silently dropping
    it from the dict passed to the root agent."""
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
    }

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes._build_planning_agent"
        ) as mock_build_agent,
    ):
        mock_agent = mock_build_agent.return_value
        mock_agent.ainvoke = AsyncMock(
            return_value={
                "remediations": {},
                "requires_edges": {},
                "migration_plans": {},
            }
        )
        result = await root_deepagent_node(state, config)
        seeded_state = mock_agent.ainvoke.await_args.args[0]

    assert set(seeded_state["targets"]) == {"lodash", "companion-dep"}
    assert seeded_state["targets"]["companion-dep"] == {
        "target_dep": "companion-dep",
        "addresses": [],
        "current_range": "^2.0.0",
        "tier": None,
    }
    # Known target keeps its real addresses/current_range untouched.
    assert seeded_state["targets"]["lodash"]["addresses"] == ["lodash"]
    assert result["targets"]["companion-dep"]["addresses"] == []


@pytest.mark.asyncio
async def test_root_deepagent_node_recursion_limit_returns_graceful_fallback():
    """Spec D10: every bound (recursion limit, correction-round cap, group
    cap) must fail honestly instead of crashing the job. A real
    GraphRecursionError from the planning agent's ainvoke must not
    propagate -- it must degrade to the same shape root_deepagent_node
    already returns on other paths, with remediations/requires_edges/
    migration_plans wiped since nothing from the aborted run is
    trustworthy."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
            return_value="/tmp/fake-work/repo",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes._build_planning_agent"
        ) as mock_build_agent,
    ):
        mock_agent = mock_build_agent.return_value
        mock_agent.ainvoke = AsyncMock(
            side_effect=GraphRecursionError("Recursion limit reached")
        )
        result = await root_deepagent_node(
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
            },
            config,
        )

    assert result["remediations"] == {}
    assert result["requires_edges"] == {}
    assert result["migration_plans"] == {}
    assert "lodash" in result["targets"]


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
            return_value=VerificationResult(installed=True, finding_resolved=True)
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
        AsyncMock(return_value=VerificationResult(installed=True, tested=False)),
    ):
        result = await group_and_verify_gate(state, config)

    assert result["retry_targets"] == ["lodash"]
    assert result["correction_rounds"] == 1
    assert "lodash" not in {
        k: v for k, v in result["remediations"].items() if v["status"] == "fixed"
    }


@pytest.mark.asyncio
async def test_group_and_verify_gate_routes_never_dispatched_companion_to_retry():
    """A companion named only via `requires` (never independently selected
    by select_remediation_targets, never dispatched by the root) has NO
    Remediation record at all yet -- this is not the same as
    dispatched-and-failed. While correction_rounds is under the cap,
    group_and_verify_gate must route the missing member into retry_targets
    instead of immediately failing the whole group. The already-dispatched
    sibling must be left untouched: not marked failed, not settled this
    round (the outer state's _merge_replace reducer preserves it across
    rounds since this round's return doesn't mention it)."""
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
    the round after root_deepagent_node's retry-mode branch redispatches it
    by the synthesized target name), the group has a Remediation record for
    every member and proceeds to normal replay/verify instead of staying
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
            return_value=VerificationResult(installed=True, finding_resolved=True)
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
        AsyncMock(return_value=green),
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
        route_after_group_verify({"retry_targets": ["lodash"]}) == "root_deepagent_node"
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
    # (see test_replay.py) -- pr_and_persist_node now cleans up via
    # shutil.rmtree(os.path.dirname(work_dir)), so a test double shaped any
    # other way (e.g. a bare tmp_path, not tmp_path/repo) would make that
    # cleanup target something far too broad, like a shared pytest tmp root.
    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)
    with open(os.path.join(work_dir, "package.json"), "w") as f:
        f.write('{"dependencies": {"lodash": "^4.17.11"}}')

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
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
        return_value=work_dir,
    ):
        result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_awaited_once()
    assert result == {"remediation_result_id": "rid-1"}
    # Cleanup must target the mkdtemp root copy_repo actually created, not
    # something broader.
    assert not os.path.exists(mkdtemp_root)
