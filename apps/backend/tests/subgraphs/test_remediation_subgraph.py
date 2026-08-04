"""Blackbox integration tests for the planner/deepagent remediation
subgraph (Task 10, task-decomposition rework).

Requires Docker. Run with:
    uv run pytest tests/subgraphs/test_remediation_subgraph.py -v

The subgraph is now five nodes:
    START -> classify_targets_node -> investigate_node ->
    root_deepagent_node -> group_and_verify_gate ->
    (route: root_deepagent_node | pr_and_persist_node) -> END

What is real:
- LangGraph wiring across all five nodes, including the retry loop back
  from `group_and_verify_gate` to `root_deepagent_node` (investigate_node
  runs exactly once per job -- retries never revisit it, per the graph's
  edges).
- `investigate_node`'s own fan-out/state-merge logic (only its leaf
  `investigate_target` call is stubbed -- see below).
- `root_deepagent_node`'s plan/outcome -> Remediation conversion
  (`_remediations_from_plans`), `connected_groups`, `group_and_verify_gate`'s
  real deterministic verify/retry loop (`replay_and_verify_group` against
  the container mock), `pr_and_persist_node`'s PR-title/consent gating, and
  real MongoDB persistence (`result_dao`, via the session testcontainer).

What is mocked, at the boundary the task-10 brief specifies (no real LLM,
no real `deepagents` machinery, no `gh`/network calls anywhere in this
file):
- `classify.classify_target` (stubbed to always return tier="r1" --
  tier classification itself is covered by test_classify.py).
- `investigate.investigate_target` (stubbed to a fixed, deterministic
  TargetInvestigation with `migration_needed=False` -- investigation
  content is irrelevant here since `_build_planning_agent` is mocked out
  entirely below it, so nothing downstream ever reads
  `state["investigations"]` for real. Covered for real by
  test_investigate.py).
- `deepagent.nodes._build_planning_agent` -- the monolithic root+nested
  `deepagents` agent from the old architecture is gone; the new planning
  agent is built fresh per `root_deepagent_node` call and only exposed
  through this one factory function. `_FakePlanningAgent` below stands in
  for its return value: something with an `ainvoke(state, config)` that
  returns `{"migration_plans": ..., "outcomes": ..., "requires_edges":
  ...}` shaped exactly like `root_deepagent_node` expects (see
  deepagent/nodes.py). A `plan_for(dep, target_dict)` callback decides
  each round's committed plan/outcome/requires per target actually present
  in that round's `state["targets"]`, so one instance transparently drives
  correction-round retries and multi-target rounds without any fake chat
  model, `task()` dispatch, or nested `CompiledSubAgent` machinery -- all
  of which no longer exists in this architecture.

DROPPED from the old suite (both tested machinery this rework removes
entirely):
- The "parallel task() calls in one turn" test asserted that
  `RemediationDeepAgentState`'s reducers survived two concurrent
  `task()`-call writes landing in one root superstep. That specific race
  (two independent nested-agent `Command` writes merging in one LangGraph
  step) doesn't exist in the new shape: `_build_planning_agent`'s single
  `ainvoke` call already returns one dict covering every dispatched target
  in that round, so there is no separate per-target write to race. The
  still-relevant assertion -- that dispatching two independent targets in
  one round handles correctly -- is folded into
  `test_consent_false_opens_zero_prs_across_every_group` below, which
  dispatches two uncoupled targets in a single fake-agent call and checks
  `fake_agent.calls[0]["targets"]` covers both.
- The "nested worker writes a real file via FilesystemBackend" test
  exercised `remediate_target`'s per-target `create_deep_agent` +
  `FilesystemBackend(virtual_mode=True)` combo, which is gone. Real
  filesystem-editing now happens inside `codemod_adapter`
  (`subagent_wrapper.build_codemod_subagent`), which per the task-10
  brief's mocking boundary never runs in this file (it lives behind the
  mocked `_build_planning_agent`). That real-write behavior belongs to a
  `subagent_wrapper`-focused test, not this graph-wiring integration test;
  today it only has shape-level coverage in
  tests/unit/subgraphs/remediation/test_subagent_wrapper.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.remediation.graph import build_remediation_subgraph
from src.models.conductor import EvidenceRef, FindingNote
from src.models.remediation import (
    MigrationPlan,
    MigrationTask,
    ReleaseDigest,
    TargetInvestigation,
)
from src.models.results import AnalysisResult, PrepResult

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Deterministic stubs for the two leaf LLM call sites upstream of planning
# (classify_target, investigate_target) -- neither is exercised by these
# tests, they're just containment so nothing here ever reaches a real LLM
# or the `gh` CLI. Both are covered for real by test_classify.py and
# test_investigate.py respectively.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _classify_everything_as_r1():
    from src.main_graph.subgraphs.remediation.classify import TargetClassification

    with patch(
        "src.main_graph.subgraphs.remediation.classify.classify_target",
        AsyncMock(
            return_value=TargetClassification(
                tier="r1", rationale="test fixture - always dispatchable"
            )
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _investigate_without_network_or_llm():
    """Yields the AsyncMock itself (not just the patch context) so tests
    can assert it was actually called -- proving `investigate_node` really
    ran as a real step in the compiled graph, not merely that downstream
    nodes tolerate a missing `investigations` channel."""

    async def _fake_investigate_target(
        target, repo_path, dependency_graph, container, docker_image
    ):
        return TargetInvestigation(
            target_dep=target.target_dep,
            dependents=[],
            call_sites=[],
            release=ReleaseDigest(
                from_version=target.current_range,
                to_version=None,
                migration_needed=False,
            ),
        )

    mock = AsyncMock(side_effect=_fake_investigate_target)
    with patch(
        "src.main_graph.subgraphs.remediation.investigate.investigate_target", mock
    ):
        yield mock


# ---------------------------------------------------------------------------
# Fake planning agent -- stand-in for `_build_planning_agent`'s return value
# ---------------------------------------------------------------------------


class _FakePlanningAgent:
    """Replaces the real `deepagents` planning agent at the exact boundary
    `root_deepagent_node` calls it through: an object with an
    `ainvoke(state, config)` coroutine. `plan_for(dep, target_dict)` is
    invoked once per target present in that round's `state["targets"]` and
    returns a dict with a required "plan" (a MigrationPlan.model_dump())
    and optional "outcome" (a RemediationOutcome.model_dump()) and
    "requires" (list[str]) -- exactly the pieces `root_deepagent_node`
    reads off a real agent's result. `self.calls` records every round's
    seeded state for assertions about how many rounds ran and what each
    round dispatched."""

    def __init__(self, plan_for: Callable[[str, dict], dict[str, Any]]) -> None:
        self._plan_for = plan_for
        self.calls: list[dict] = []

    async def ainvoke(self, state: dict, config: Any = None) -> dict:
        self.calls.append(state)
        migration_plans: dict[str, dict] = {}
        outcomes: dict[str, dict] = {}
        requires_edges: dict[str, list[str]] = {}
        for dep, target_dict in state["targets"].items():
            spec = self._plan_for(dep, target_dict)
            migration_plans[dep] = spec["plan"]
            if spec.get("outcome") is not None:
                outcomes[dep] = spec["outcome"]
            if spec.get("requires"):
                requires_edges[dep] = spec["requires"]
        return {
            "migration_plans": migration_plans,
            "outcomes": outcomes,
            "requires_edges": requires_edges,
        }


def _patch_planning_agent(agent: _FakePlanningAgent):
    return patch.object(
        deepagent_nodes, "_build_planning_agent", MagicMock(return_value=agent)
    )


def _bump_plan(dep: str, to_range: str, requires: list[str] | None = None) -> dict:
    return MigrationPlan(
        target_dep=dep,
        tier_hint="r1",
        tasks=[
            MigrationTask(kind="bump", rationale="clean upgrade", to_range=to_range)
        ],
        requires=requires or [],
    ).model_dump()


# ---------------------------------------------------------------------------
# Fixtures / seed data
# ---------------------------------------------------------------------------


def _write_repo(tmp_path: Path, direct_deps: dict[str, str]) -> str:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    package_json = {
        "name": "test-project",
        "version": "1.0.0",
        "dependencies": direct_deps,
    }
    import json

    (repo_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    return str(repo_dir)


def _seed_prep(job_id: str, repo_path: str, direct_deps: dict[str, str]) -> PrepResult:
    return PrepResult(
        job_id=job_id,
        repo_path=repo_path,
        project_metadata={
            "name": "test-project",
            "package_manager": "npm",
            "direct_dependencies_count": len(direct_deps),
            "transitive_dependencies_count": 0,
        },
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": direct_deps, "packages": {}},
    )


def _seed_analysis(job_id: str, findings: list[FindingNote]) -> AnalysisResult:
    return AnalysisResult(
        job_id=job_id,
        concern="dependency health",
        findings=findings,
        evidence_bundle_ids=[],
        iteration_count=1,
    )


def _finding(dep_name: str, description: str) -> FindingNote:
    return FindingNote(
        dep_name=dep_name,
        severity="critical",
        description=description,
        evidence=[EvidenceRef(tool="npm_audit", url=None, log_snippet="")],
    )


class _FakeGitPR:
    def __init__(self, pr_url: str = "https://github.com/acme/widgets/pull/1"):
        self.open_pr = AsyncMock(return_value=pr_url)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pure_bump_target_ships_one_fixed_pr(
    tmp_path, result_dao, subgraph_config, _investigate_without_network_or_llm
):
    """A single, uncoupled target with no requires signal: the planning
    agent commits a bump-only plan (no outcome), the deterministic gate
    verifies green against the container mock, and exactly one PR labeled
    "bump" ships. Also asserts `investigate_node` itself actually ran as a
    real step of the compiled graph (not just that downstream nodes
    tolerate a missing `investigations` channel)."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    repo_path = _write_repo(tmp_path, {"leftpad": "1.0.0"})
    prep = _seed_prep(job_id, repo_path, {"leftpad": "1.0.0"})
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(job_id, [_finding("leftpad", "leftpad has a known CVE")])
    await result_dao.save_analysis(analysis)

    def _plan_for(dep: str, target: dict) -> dict:
        assert dep == "leftpad"
        return {"plan": _bump_plan("leftpad", "^1.0.1")}

    fake_agent = _FakePlanningAgent(_plan_for)

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = True
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with _patch_planning_agent(fake_agent):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    assert len(remediation_result.remediations) == 1
    r = remediation_result.remediations[0]
    assert r.target_dep == "leftpad"
    assert r.status == "fixed"
    assert r.to_range == "^1.0.1"
    assert r.branch == f"remediation/{job_id[:8]}-leftpad"
    assert r.pr_url == "https://github.com/acme/widgets/pull/1"

    fake_git_pr.open_pr.assert_awaited_once()
    title = fake_git_pr.open_pr.await_args.args[2]
    assert "bump" in title
    assert len(fake_agent.calls) == 1

    # investigate_node is a real step in the compiled graph -- it must
    # have actually fanned out to investigate_target for "leftpad" before
    # root_deepagent_node's (mocked) planning agent ever ran.
    _investigate_without_network_or_llm.assert_awaited_once()
    investigated_target = _investigate_without_network_or_llm.await_args.args[0]
    assert investigated_target.target_dep == "leftpad"


async def test_requires_signal_pulls_in_a_non_finding_companion(
    tmp_path, result_dao, subgraph_config
):
    """The eslint/eslint-plugin-react scenario from the spec: the planning
    agent's plan for target A emits requires=["B"], B has no FindingNote,
    group_and_verify_gate routes the never-dispatched companion through a
    retry round, and both end up in ONE group/PR with B's
    Remediation.required_by == ["A"]."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"eslint": "7.0.0", "eslint-plugin-react": "7.20.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    # Only eslint has a finding -- eslint-plugin-react is pulled in purely
    # via the requires signal, never independently selected.
    analysis = _seed_analysis(job_id, [_finding("eslint", "eslint has a known CVE")])
    await result_dao.save_analysis(analysis)

    def _plan_for(dep: str, target: dict) -> dict:
        if dep == "eslint":
            return {
                "plan": _bump_plan(
                    "eslint", "^8.0.0", requires=["eslint-plugin-react"]
                ),
                "requires": ["eslint-plugin-react"],
            }
        if dep == "eslint-plugin-react":
            return {"plan": _bump_plan("eslint-plugin-react", "^7.20.1")}
        msg = f"unexpected dep dispatched: {dep}"
        raise AssertionError(msg)

    fake_agent = _FakePlanningAgent(_plan_for)

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = True
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with _patch_planning_agent(fake_agent):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    by_dep = {r.target_dep: r for r in remediation_result.remediations}
    assert set(by_dep) == {"eslint", "eslint-plugin-react"}
    assert by_dep["eslint"].status == "fixed"
    assert by_dep["eslint-plugin-react"].status == "fixed"
    assert by_dep["eslint-plugin-react"].required_by == ["eslint"]
    assert by_dep["eslint"].required_by == []

    # One shared PR for the whole connected group.
    fake_git_pr.open_pr.assert_awaited_once()
    title = fake_git_pr.open_pr.await_args.args[2]
    assert "eslint" in title
    assert "eslint-plugin-react" in title
    assert by_dep["eslint"].branch == by_dep["eslint-plugin-react"].branch
    assert by_dep["eslint"].pr_url == by_dep["eslint-plugin-react"].pr_url

    # Round 1 dispatches eslint alone and discovers the companion via
    # requires_edges; group_and_verify_gate then retries the never-
    # dispatched companion, so round 2 dispatches eslint-plugin-react.
    assert len(fake_agent.calls) == 2
    assert set(fake_agent.calls[0]["targets"]) == {"eslint"}
    assert set(fake_agent.calls[1]["targets"]) == {"eslint-plugin-react"}


async def test_correction_round_retries_then_gives_up_at_cap(
    tmp_path, result_dao, subgraph_config
):
    """A target whose verification always fails: assert
    group_and_verify_gate retries it up to _MAX_CORRECTION_ROUNDS, then
    ships status="failed" with a reason, and that the planning agent was
    invoked exactly (1 + _MAX_CORRECTION_ROUNDS) times, not more."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"always-broken": "1.0.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(
        job_id, [_finding("always-broken", "always-broken has a known CVE")]
    )
    await result_dao.save_analysis(analysis)

    # Every container.run call (verify_working_copy's install step during
    # every group verification) fails, so replay_and_verify_group can never
    # go green.
    subgraph_config["configurable"]["container"].run = AsyncMock(
        return_value=(1, "", "npm install failed")
    )

    def _plan_for(dep: str, target: dict) -> dict:
        assert dep == "always-broken"
        return {"plan": _bump_plan("always-broken", "^2.0.0")}

    fake_agent = _FakePlanningAgent(_plan_for)

    with _patch_planning_agent(fake_agent):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    assert len(remediation_result.remediations) == 1
    r = remediation_result.remediations[0]
    assert r.target_dep == "always-broken"
    assert r.status == "failed"
    assert r.skip_reason == "verification failed after max correction rounds"

    assert len(fake_agent.calls) == 1 + deepagent_nodes._MAX_CORRECTION_ROUNDS


async def test_consent_false_opens_zero_prs_across_every_group(
    tmp_path, result_dao, subgraph_config
):
    """Two independent fixed targets, remediate=False in configurable:
    assert the fake git_pr's open_pr is never called, and both
    Remediation records still have branch=None, pr_url=None, and a real
    (non-default) verification result -- proves group_and_verify_gate's
    deterministic verification runs unconditionally, and only
    pr_and_persist_node's PR step is gated on consent. Also folds in the
    dropped "parallel dispatch" test's assertion: both targets are
    dispatched together in a single planning-agent round."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"pkg-c": "1.0.0", "pkg-d": "2.0.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(
        job_id,
        [
            _finding("pkg-c", "pkg-c has a known CVE"),
            _finding("pkg-d", "pkg-d has a known CVE"),
        ],
    )
    await result_dao.save_analysis(analysis)

    ranges = {"pkg-c": "^1.0.1", "pkg-d": "^2.0.1"}

    def _plan_for(dep: str, target: dict) -> dict:
        return {"plan": _bump_plan(dep, ranges[dep])}

    fake_agent = _FakePlanningAgent(_plan_for)

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = False
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with _patch_planning_agent(fake_agent):
        graph = build_remediation_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("remediation_result_id")
    remediation_result = await result_dao.get_remediation(
        result["remediation_result_id"]
    )
    by_dep = {r.target_dep: r for r in remediation_result.remediations}
    assert set(by_dep) == {"pkg-c", "pkg-d"}
    for r in by_dep.values():
        assert r.status == "fixed"
        assert r.branch is None
        assert r.pr_url is None
        assert r.verification.installed is True

    fake_git_pr.open_pr.assert_not_called()
    assert len(fake_agent.calls) == 1
    assert set(fake_agent.calls[0]["targets"]) == {"pkg-c", "pkg-d"}
