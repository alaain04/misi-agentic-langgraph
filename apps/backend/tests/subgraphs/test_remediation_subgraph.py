"""Blackbox integration tests for the remediation subgraph, rewritten for
the flatten-planning-execution rework
(docs/superpowers/specs/2026-08-08-remediation-flatten-planning-execution.md).

Requires Docker. Run with:
    uv run pytest tests/subgraphs/test_remediation_subgraph.py -v

The subgraph is now six nodes:
    START -> select_targets_node -> research_releases_node ->
    build_migration_plan_node -> remediate_targets_node ->
    group_and_verify_gate -> (route: remediate_targets_node |
    pr_and_persist_node) -> END

What is real:
- LangGraph wiring across all six nodes, including the retry loop back from
  `group_and_verify_gate` to `remediate_targets_node` (`build_migration_plan_node`
  runs exactly once per job -- retries never revisit it, they reuse the
  existing `migration_plans` state, per spec D3).
- `remediate_targets_node`'s plan/outcome -> Remediation conversion
  (`_assemble_remediations`), `connected_groups`, `group_and_verify_gate`'s
  real deterministic verify/retry loop (`replay_and_verify_group` against
  the container mock), `pr_and_persist_node`'s PR-title/consent gating, and
  real MongoDB persistence (`result_dao`, via the session testcontainer).

What is mocked, at the boundaries this architecture now has (no real LLM,
no real `deepagents` machinery, no real `npm`/`gh`/codegraph/network calls
anywhere in this file):
- `select_targets.resolve_package_info`, `select_targets.compute_blast_radius`,
  and `select_targets._index_codegraph` -- `select_targets_node`'s three I/O
  boundaries (npm registry lookup, codegraph blast-radius query, codegraph
  indexing), stubbed to a deterministic "nothing resolved, no blast-radius
  data" result so the node's own selection/anchoring/r3-tier logic still
  runs for real against real findings and a real dependency graph (covered
  in full by test_select_targets.py).
- `release_research._llm` -- the ONE structured-output call
  `research_releases_node`'s loop makes per target, stubbed to
  immediately finalize with `migration_needed=False` (a clean bump, no
  breaking changes) so the loop's real control flow -- one iteration,
  straight to finalize, `ReleaseDigest` assembly -- still runs as a real
  step (the multi-iteration/tool-calling path is covered for real by
  test_release_research.py).
- `plan.build_plans_for_targets` -- the ONE batched structured-output
  planning call. Patched at both `plan.build_plans_for_targets` (used by
  `build_migration_plan_node`, the initial batch) and
  `deepagent.nodes.build_plans_for_targets` (used by `remediate_targets_node`'s
  retry-discovered-companion fallback, a single-target call). `_fake_planner`
  drives both from one `plan_for(dep, target_dict)` callback and records
  every call's dep set into `plan_calls`.
- `deepagent.nodes.build_execution_agent` -- the ONE flat execution agent,
  invoked directly per group (never via deepagents' `task()`, which no
  longer exists anywhere in this path). `_FakeExecutionAgent` stands in for
  its return value: an object with `ainvoke(state, config)` that parses the
  group's dep names straight out of `_format_group_message`'s own text
  (a "- {dep}: kind=..." line per target) and returns `{"outcomes": ...}`
  for whichever of those deps `plan_for` gave an `outcome` -- exactly the
  shape `_run_group` expects. `_FakeExecutionAgent.calls` (aliased below as
  `exec_calls`) records the dep list dispatched to it on every distinct
  group invocation, which is now the right level to assert dispatch
  behavior at: independent groups get separate concurrent agent
  invocations (spec goal 2), not one fused call covering every target in a
  round the way the old single-agent design did.

DROPPED from the prior suite (machinery this rework removes entirely):
- Assertions phrased around "the planning agent's calls" as a single
  combined plan+dispatch boundary no longer make sense -- planning and
  execution are now two independently-mockable steps with different
  cardinality (see `test_correction_round_retries_then_gives_up_at_cap`,
  where planning happens exactly once but execution retries
  `1 + _MAX_CORRECTION_ROUNDS` times -- the concrete benefit of spec D3:
  a retry no longer re-plans, only re-executes with the existing plan).
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
from src.main_graph.subgraphs.remediation.release_research import (
    ReleaseResearchDecision,
)
from src.models.conductor import EvidenceRef, FindingNote
from src.models.remediation import MigrationPlan, MigrationTask, RemediationOutcome
from src.models.results import AnalysisResult, PrepResult

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Deterministic stubs for select_targets_node's and research_releases_node's
# I/O/LLM boundaries -- not exercised by these tests, just containment so
# nothing here ever reaches a real npm/gh/codegraph/LLM call. Covered for
# real by test_select_targets.py and test_release_research.py.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _select_and_research_everything_as_clean_bump():
    """Stubs select_targets_node's and research_releases_node's I/O/LLM
    boundaries so both run for real as compiled graph steps (selection
    logic, tier check, the research loop's finalize path) without ever
    reaching a real npm/gh/codegraph/LLM call. Yields the resolve mock so
    tests can assert it was actually invoked."""
    resolve_mock = AsyncMock(return_value=(None, None))
    llm_mock = MagicMock()
    llm_mock.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=ReleaseResearchDecision(
            tool_calls=[],
            finalize=True,
            migration_needed=False,
            migration_guide="",
            breaking_changes=[],
            reasoning="test fixture - always a clean bump",
        )
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            resolve_mock,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.compute_blast_radius",
            AsyncMock(return_value={"available": False}),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.select_targets._index_codegraph",
            AsyncMock(return_value=True),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.release_research._llm", llm_mock
        ),
    ):
        yield resolve_mock


# ---------------------------------------------------------------------------
# Fake planning + execution boundaries
# ---------------------------------------------------------------------------


def _bump_spec(
    dep: str, to_range: str, requires: list[str] | None = None
) -> dict[str, Any]:
    """A plan_for() return value for the common case: a clean bump that the
    execution agent commits an outcome for on the first try (bump is folded
    into the execution agent now, spec goal 3 -- it always needs an
    outcome, never a bare data-carry)."""
    plan = MigrationPlan(
        target_dep=dep,
        tier_hint="r1",
        tasks=[
            MigrationTask(kind="bump", rationale="clean upgrade", to_range=to_range)
        ],
        requires=requires or [],
    ).model_dump()
    outcome = RemediationOutcome(strategy="bump", to_range=to_range).model_dump()
    return {"plan": plan, "outcome": outcome}


def _fake_build_plans_for_targets(
    plan_for: Callable[[str, dict], dict[str, Any]], plan_calls: list[set[str]]
):
    async def _build(targets: dict[str, dict], investigations: dict[str, dict]):
        plan_calls.append(set(targets))
        return {dep: plan_for(dep, t)["plan"] for dep, t in targets.items()}

    return _build


def _deps_from_group_message(content: str) -> list[str]:
    """Parse the dep names straight out of _format_group_message's own
    "- {dep}: kind=..." lines -- the only way a fake execution agent can
    know which targets it was asked to handle, since build_execution_agent
    itself is never given a dep list directly (only work_dir/container/
    image/package_manager; the group is threaded through the agent's
    initial message, not its constructor)."""
    deps = []
    for line in content.splitlines():
        if line.startswith("- ") and ":" in line:
            deps.append(line[2:].split(":", 1)[0])
    return deps


class _FakeExecutionAgent:
    def __init__(
        self,
        plan_for: Callable[[str, dict], dict[str, Any]],
        exec_calls: list[list[str]],
    ) -> None:
        self._plan_for = plan_for
        self._exec_calls = exec_calls

    async def ainvoke(self, state: dict, config: Any = None) -> dict:
        deps = _deps_from_group_message(state["messages"][0]["content"])
        self._exec_calls.append(deps)
        outcomes: dict[str, dict] = {}
        for dep in deps:
            spec = self._plan_for(dep, None)
            if spec.get("outcome") is not None:
                outcomes[dep] = spec["outcome"]
        return {"outcomes": outcomes}


def _patch_planner_and_executor(
    plan_for: Callable[[str, dict], dict[str, Any]],
):
    """Patches both mockable boundaries with one shared plan_for callback.
    Returns (plan_calls, exec_calls) -- lists the caller can assert against
    after the `with` block, plus the three patch context managers to enter."""
    plan_calls: list[set[str]] = []
    exec_calls: list[list[str]] = []
    fake_build_plans = _fake_build_plans_for_targets(plan_for, plan_calls)
    patches = (
        patch(
            "src.main_graph.subgraphs.remediation.plan.build_plans_for_targets",
            fake_build_plans,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_plans_for_targets",
            fake_build_plans,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.nodes.build_execution_agent",
            side_effect=lambda *a, **k: _FakeExecutionAgent(plan_for, exec_calls),
        ),
    )
    return plan_calls, exec_calls, patches


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
        package_manager="npm",
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
    tmp_path,
    result_dao,
    subgraph_config,
    _select_and_research_everything_as_clean_bump,
):
    """A single, uncoupled target with no requires signal: one batched
    planning call commits a bump plan + outcome, the deterministic gate
    verifies green against the container mock, and exactly one PR labeled
    "bump" ships. Also asserts select_targets_node itself actually ran as a
    real step of the compiled graph (not just that downstream nodes
    tolerate a missing `investigations` channel)."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    repo_path = _write_repo(tmp_path, {"leftpad": "1.0.0"})
    prep = _seed_prep(job_id, repo_path, {"leftpad": "1.0.0"})
    await result_dao.save_prep(prep)
    analysis = _seed_analysis(job_id, [_finding("leftpad", "leftpad has a known CVE")])
    await result_dao.save_analysis(analysis)

    def _plan_for(dep: str, target: dict | None) -> dict:
        assert dep == "leftpad"
        return _bump_spec("leftpad", "^1.0.1")

    plan_calls, exec_calls, patches = _patch_planner_and_executor(_plan_for)

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = True
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with patches[0], patches[1], patches[2]:
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
    assert plan_calls == [{"leftpad"}]
    assert exec_calls == [["leftpad"]]

    # select_targets_node is a real step in the compiled graph -- it must
    # have actually resolved "leftpad" before build_migration_plan_node's
    # (mocked) planning call ever ran.
    _select_and_research_everything_as_clean_bump.assert_awaited_once()
    # resolve_package_info's first positional arg is the dep-name string
    # itself (select_targets._resolve_bounded calls it as
    # resolve_package_info(target.target_dep, ...)), not the target object.
    resolved_dep = _select_and_research_everything_as_clean_bump.await_args.args[0]
    assert resolved_dep == "leftpad"


async def test_requires_signal_pulls_in_a_non_finding_companion(
    tmp_path, result_dao, subgraph_config
):
    """The eslint/eslint-plugin-react scenario from the spec: the batched
    planning call commits eslint's MigrationPlan with
    requires=["eslint-plugin-react"], remediate_targets_node derives
    requires_edges from that field, eslint-plugin-react has no FindingNote
    and is never in the initial target set, group_and_verify_gate routes
    the never-dispatched companion through a retry round (which plans it
    fresh via the single-target fallback, then dispatches it), and both end
    up in ONE group/PR with eslint-plugin-react's Remediation.required_by
    == ["eslint"]."""
    job_id = f"rem-{uuid.uuid4().hex[:8]}"
    direct = {"eslint": "7.0.0", "eslint-plugin-react": "7.20.0"}
    repo_path = _write_repo(tmp_path, direct)
    prep = _seed_prep(job_id, repo_path, direct)
    await result_dao.save_prep(prep)
    # Only eslint has a finding -- eslint-plugin-react is pulled in purely
    # via the requires signal, never independently selected.
    analysis = _seed_analysis(job_id, [_finding("eslint", "eslint has a known CVE")])
    await result_dao.save_analysis(analysis)

    def _plan_for(dep: str, target: dict | None) -> dict:
        if dep == "eslint":
            return _bump_spec("eslint", "^8.0.0", requires=["eslint-plugin-react"])
        if dep == "eslint-plugin-react":
            return _bump_spec("eslint-plugin-react", "^7.20.1")
        msg = f"unexpected dep dispatched: {dep}"
        raise AssertionError(msg)

    plan_calls, exec_calls, patches = _patch_planner_and_executor(_plan_for)

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = True
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with patches[0], patches[1], patches[2]:
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

    # Round 1's ONE batched planning call covers only eslint (the only
    # initial target) and dispatches it alone; group_and_verify_gate then
    # retries the never-dispatched companion, so round 2 plans (via the
    # single-target fallback) and dispatches eslint-plugin-react.
    assert plan_calls == [{"eslint"}, {"eslint-plugin-react"}]
    assert exec_calls == [["eslint"], ["eslint-plugin-react"]]


async def test_correction_round_retries_then_gives_up_at_cap(
    tmp_path, result_dao, subgraph_config
):
    """A target whose verification always fails: assert
    group_and_verify_gate retries it up to _MAX_CORRECTION_ROUNDS, then
    ships status="failed" with a reason. Planning happens exactly ONCE
    (spec D3: a retry reuses the existing MigrationPlan instead of
    re-planning); only execution is retried, `1 + _MAX_CORRECTION_ROUNDS`
    times."""
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

    def _plan_for(dep: str, target: dict | None) -> dict:
        assert dep == "always-broken"
        return _bump_spec("always-broken", "^2.0.0")

    plan_calls, exec_calls, patches = _patch_planner_and_executor(_plan_for)

    with patches[0], patches[1], patches[2]:
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

    assert plan_calls == [{"always-broken"}]
    expected_rounds = 1 + deepagent_nodes._MAX_CORRECTION_ROUNDS
    assert exec_calls == [["always-broken"]] * expected_rounds


async def test_consent_false_opens_zero_prs_across_every_group(
    tmp_path, result_dao, subgraph_config
):
    """Two independent fixed targets, remediate=False in configurable:
    assert the fake git_pr's open_pr is never called, and both Remediation
    records still have branch=None, pr_url=None, and a real (non-default)
    verification result -- proves group_and_verify_gate's deterministic
    verification runs unconditionally, and only pr_and_persist_node's PR
    step is gated on consent. Also asserts the two independent targets get
    separate concurrent execution-agent invocations (one group each,
    unlike the old single-agent design that fused every target in a round
    into one call) even though planning covers both in a single batched
    call."""
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

    def _plan_for(dep: str, target: dict | None) -> dict:
        return _bump_spec(dep, ranges[dep])

    plan_calls, exec_calls, patches = _patch_planner_and_executor(_plan_for)

    fake_git_pr = _FakeGitPR()
    subgraph_config["configurable"]["remediate"] = False
    subgraph_config["configurable"]["git_pr"] = fake_git_pr

    with patches[0], patches[1], patches[2]:
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
    assert plan_calls == [{"pkg-c", "pkg-d"}]
    assert {tuple(c) for c in exec_calls} == {("pkg-c",), ("pkg-d",)}
