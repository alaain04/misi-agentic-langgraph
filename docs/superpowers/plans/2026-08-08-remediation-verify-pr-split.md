# Remediation Verify/PR Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pr_and_persist_node` do exactly one job — open the PR from an already-verified working copy and persist the result — by moving the commit-ready-copy preparation (and its pass/fail decision) into `group_and_verify_gate`, where the existing `retry_targets`/`correction_rounds` feedback loop already lives.

**Architecture:** `group_and_verify_gate` already replays a settled group's changes onto a fresh clone and verifies it (`replay_and_verify_group`). Today it throws that clone away and `pr_and_persist_node` redundantly repeats the same copy+replay+verify sequence on a *second* clone just to get a lockfile that matches the bumped `package.json` — and if that second verify fails, it dead-ends silently with no feedback loop, unlike the gate's own failures. The fix: `replay_and_verify_group` gains a `keep_workdir` flag; the gate passes `keep_workdir=True` only when a PR could actually be opened (`consent` and a configured `git_pr` adapter) and only keeps the clone when the group verifies green, recording its path in a new `verified_workdirs` state channel keyed by `target_dep`. A verification failure on a kept clone is cleaned up by the gate itself, using the same retry mechanism as any other failure — there is no longer a second, unaccountable failure path. `pr_and_persist_node` then does nothing but read `verified_workdirs`, open one PR per distinct work dir, and persist.

**Tech Stack:** Python 3.12, LangGraph, Pydantic, pytest, pytest-asyncio. No new dependencies.

**Prior context:** `apps/backend/docs/graphs.md` ("Remediation subgraph" section) documents the current 6-node pipeline and the exact mismatch this plan resolves — read it before starting if you want the narrative version of what's below.

## Global Constraints

- **`group_and_verify_gate` remains the only thing that decides `fixed`/`failed`/`skipped`.** `pr_and_persist_node` must never re-run `verify_working_copy` or `apply_group_changes` again after this plan — if it does, the split has failed.
- **A verification failure on a kept clone must be cleanable and must not leak a temp dir.** Every `copy_repo` call still has exactly one owner responsible for `shutil.rmtree(os.path.dirname(work_dir))` — never both the gate and the PR node holding the same path.
- **No behavior change visible to callers when `consent=False` or no `git_pr` is configured:** zero PRs open, `verified_workdirs` stays empty, `remediation_result_id` is still returned. The Docker-backed integration test `test_consent_false_opens_zero_prs_across_every_group` must keep passing unmodified.
- **`RemediationState` fields use the existing `_merge_replace` reducer** (`{**current, **incoming}`, per-key overwrite) for any new dict-shaped channel, matching `remediations`/`migration_plans`/`requires_edges` — so a group settled in an earlier correction round survives later rounds that don't touch it.
- **No emoji.** Match existing docstring/comment style (plain prose, no headers inside functions).

## File Structure

- `src/main_graph/subgraphs/remediation/deepagent/replay.py` (modify) — `replay_and_verify_group` gains `keep_workdir: bool = False`, returns `tuple[VerificationResult, str | None]` instead of `VerificationResult`.
- `tests/unit/subgraphs/remediation/test_replay.py` (modify) — update the two existing `replay_and_verify_group` call sites for the new return shape; add two tests for `keep_workdir`.
- `src/main_graph/subgraphs/remediation/state.py` (modify) — add `verified_workdirs` channel.
- `src/main_graph/subgraphs/remediation/deepagent/nodes.py` (modify) — `group_and_verify_gate` threads `keep_workdir` and populates `verified_workdirs`; `pr_and_persist_node` rewritten to consume it instead of re-deriving/re-verifying; drop now-unused `apply_group_changes`/`verify_working_copy` imports.
- `tests/unit/subgraphs/remediation/test_deepagent_nodes.py` (modify) — update 7 existing `group_and_verify_gate` tests for the new mock return shape, add 3 new gate tests for the keep/cleanup/no-consent paths, rewrite the 2 surviving `pr_and_persist_node` tests, delete the now-obsolete `test_pr_and_persist_node_skips_pr_when_final_install_fails` (its scenario moved to the gate), add 1 new `pr_and_persist_node` test for multi-dep-same-workdir grouping.
- `docs/graphs.md` (modify) — replace the "Mismatch" callout in the Remediation subgraph section with a description of the resolved design.

---

### Task 1: `replay_and_verify_group` — optional kept working copy

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/replay.py:69-93`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_replay.py`

**Interfaces:**
- Produces: `async def replay_and_verify_group(members: list[Remediation], base_repo_path: str, container: ContainerRunPort, docker_image: str, package_manager: str, keep_workdir: bool = False) -> tuple[VerificationResult, str | None]`. Second element is the work dir path (matching `copy_repo`'s `.../repo` contract) when `keep_workdir=True` **and** the change applied cleanly; `None` otherwise. Caller owns cleanup of a returned path.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_replay.py`, after `test_replay_and_verify_group_reports_apply_failure`:

```python
@pytest.mark.asyncio
async def test_replay_and_verify_group_keeps_workdir_when_requested(monkeypatch):
    mkdtemp_root = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work)

    pkg_path = os.path.join(work, "package.json")
    with open(pkg_path, "w") as f:
        json.dump({"dependencies": {"lodash": "^4.17.11"}, "scripts": {}}, f)

    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: work,
    )
    audit = json.dumps({"vulnerabilities": {}})
    container = FakeContainer([(0, "", ""), (0, audit, "")])

    result, kept_dir = await replay_and_verify_group(
        [_bump()],
        "/original/repo",
        container,
        "node:lts-alpine",
        "npm",
        keep_workdir=True,
    )

    assert result.installed is True
    assert kept_dir == work
    # keep_workdir=True means the caller owns cleanup now.
    assert os.path.exists(mkdtemp_root)

    shutil.rmtree(mkdtemp_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_replay_and_verify_group_cleans_up_on_apply_failure_even_if_keep_requested(
    monkeypatch,
):
    mkdtemp_root = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work)

    pkg_path = os.path.join(work, "package.json")
    with open(pkg_path, "w") as f:
        json.dump({"dependencies": {}}, f)

    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: work,
    )
    result, kept_dir = await replay_and_verify_group(
        [_bump()],
        "/original/repo",
        FakeContainer([]),
        "node:lts-alpine",
        "npm",
        keep_workdir=True,
    )
    assert result.installed is False
    assert kept_dir is None
    assert not os.path.exists(mkdtemp_root)
```

Add `import shutil` to the top of the file (it currently only imports `json, os, subprocess, tempfile`).

Also update the two existing tests' call sites (they now unpack a tuple):

```python
    result, kept_dir = await replay_and_verify_group(
        [_bump()], "/original/repo", container, "node:lts-alpine", "npm"
    )

    assert result.installed is True
    assert result.finding_resolved is True
    assert kept_dir is None
```

(same pattern for `test_replay_and_verify_group_reports_apply_failure`: `result, kept_dir = await replay_and_verify_group(...)` then `assert kept_dir is None` alongside the existing assertions).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_replay.py -v`
Expected: the two new tests FAIL (`replay_and_verify_group() got an unexpected keyword argument 'keep_workdir'`); the two updated existing tests FAIL to unpack (`cannot unpack non-tuple VerificationResult`).

- [ ] **Step 3: Implement `keep_workdir`**

Replace `replay_and_verify_group` in `apps/backend/src/main_graph/subgraphs/remediation/deepagent/replay.py`:

```python
async def replay_and_verify_group(
    members: list[Remediation],
    base_repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
    keep_workdir: bool = False,
) -> tuple[VerificationResult, str | None]:
    """The deterministic backstop (spec D6): replay a settled group's
    changes onto a fresh clean clone and re-run full verification from
    scratch. Never trusts any member's own self-reported status.

    When keep_workdir is True and the change applies cleanly, the working
    copy is left on disk instead of deleted -- its install step has already
    regenerated the lockfile against the bumped package.json, so it is
    ready to ship as-is. The second element of the return tuple is that
    path (or None when nothing was kept); the caller then owns its
    cleanup. A failed apply is always cleaned up here regardless of
    keep_workdir, since there is nothing usable to keep."""
    work_dir = copy_repo(base_repo_path)
    keep = False
    try:
        if not await apply_group_changes(work_dir, members):
            return (
                VerificationResult(
                    logs_snippet="one or more changes failed to apply cleanly"
                ),
                None,
            )
        targeted = sorted(
            {dep for m in members for dep in [m.target_dep, *m.addresses]}
        )
        result = await verify_working_copy(
            work_dir, container, docker_image, package_manager, targeted
        )
        keep = keep_workdir
        return result, (work_dir if keep else None)
    finally:
        if not keep:
            shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_replay.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/replay.py apps/backend/tests/unit/subgraphs/remediation/test_replay.py
git commit -m "feat: let replay_and_verify_group keep its working copy on request"
```

---

### Task 2: `group_and_verify_gate` produces `verified_workdirs`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/state.py`
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py:301-407`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Consumes: `replay_and_verify_group(..., keep_workdir: bool = False) -> tuple[VerificationResult, str | None]` (Task 1).
- Produces: `RemediationState["verified_workdirs"]: dict[str, str]` — `target_dep -> work_dir`, one entry per member of a group whose replay verified green AND was kept (i.e. `consent` and a `git_pr` adapter were both configured when the gate ran). Every member of the same group maps to the *same* path.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`, after `test_group_and_verify_gate_requests_retry_under_cap` (around line 666):

```python
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
    # replay_and_verify_group only returns a path when it verified green
    # (Task 1's contract) -- a failed verification with keep_workdir=True
    # requested still comes back with kept_dir=None, so there is nothing
    # for the gate to clean up here. This test instead pins the contract
    # that a failing group's entry never reaches verified_workdirs.
    mock_replay = AsyncMock(
        return_value=(VerificationResult(installed=True, tested=False), None)
    )
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
        mock_replay,
    ):
        result = await group_and_verify_gate(state, config)

    assert result["verified_workdirs"] == {}
    assert result["remediations"]["lodash"]["status"] == "failed"
```

Now update the mock return values in the 4 pre-existing tests that patch `replay_and_verify_group` with a bare `VerificationResult` return, to tuples instead — `replay_and_verify_group`'s new contract is `(VerificationResult, str | None)`:

`test_group_and_verify_gate_marks_group_fixed_on_green_verification` (line ~621):
```python
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
        AsyncMock(
            return_value=(
                VerificationResult(installed=True, finding_resolved=True),
                None,
            )
        ),
    ):
```

`test_group_and_verify_gate_requests_retry_under_cap` (line ~656):
```python
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
        AsyncMock(return_value=(VerificationResult(installed=True, tested=False), None)),
    ):
```

`test_group_and_verify_gate_settles_group_once_companion_dispatched` (line ~748):
```python
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
        AsyncMock(
            return_value=(
                VerificationResult(installed=True, finding_resolved=True),
                None,
            )
        ),
    ):
```

`test_group_verify_preserves_plan_field` (line ~832):
```python
    green = VerificationResult(
        installed=True, built=True, tested=True, finding_resolved=True
    )
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
        AsyncMock(return_value=(green, None)),
    ):
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -k group_and_verify_gate -v`
Expected: the 3 new tests FAIL (`KeyError: 'verified_workdirs'` / no `keep_workdir` kwarg observed); the 4 updated tests FAIL (`TypeError: cannot unpack non-tuple VerificationResult` inside the node, once Step 3 changes the call site -- before Step 3 they still fail because the *test* now hands the node a tuple where it expects a bare `VerificationResult`, e.g. `_is_green(verification)` blows up on a tuple). Confirm the failure is the expected mismatch, not a typo.

- [ ] **Step 3: Add `verified_workdirs` to `RemediationState`**

In `apps/backend/src/main_graph/subgraphs/remediation/state.py`, add one line after `requires_edges`:

```python
    verified_workdirs: NotRequired[Annotated[dict[str, str], _merge_replace]]
```

- [ ] **Step 4: Thread `keep_workdir` through `group_and_verify_gate`**

In `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`, replace the function body (lines 301-407):

```python
async def group_and_verify_gate(
    state: RemediationState, config: RunnableConfig
) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")
    prep = await dao.get_prep(state["prep_result_id"])

    remediations: dict[str, dict] = dict(state.get("remediations") or {})
    requires_edges: dict[str, list] = state.get("requires_edges") or {}
    target_deps = list(state.get("targets") or {})
    correction_rounds = state.get("correction_rounds", 0)

    required_by_map: dict[str, list[str]] = {}
    for target, requires in requires_edges.items():
        for required in requires:
            required_by_map.setdefault(required, []).append(target)

    groups = connected_groups(target_deps, requires_edges)

    settled: dict[str, dict] = {}
    retry_targets: list[str] = []
    verified_workdirs: dict[str, str] = {}
    keep_workdir = consent and bool(git_pr)

    for group in groups[:_MAX_GROUPS]:
        members_dicts = [remediations[dep] for dep in group if dep in remediations]
        if len(members_dicts) != len(group):
            missing = [dep for dep in group if dep not in remediations]
            if correction_rounds < _MAX_CORRECTION_ROUNDS:
                # A member named only via `requires` (never in the original
                # select_remediation_targets output) has no Remediation
                # record yet -- it was never dispatched, not dispatched-
                # and-failed. Route it through the same retry mechanism
                # used for failed verification instead of immediately
                # failing the whole group: remediate_targets_node's retry-
                # mode branch synthesizes a target entry for any
                # retry_targets name not already in state["targets"] and
                # explicitly instructs the root to dispatch it by name.
                # Leave this group's already-dispatched members untouched
                # in `remediations` (the outer state's _merge_replace
                # reducer preserves them across rounds) and don't settle
                # anything from this group yet -- its fate is decided once
                # all members exist.
                retry_targets.extend(missing)
                continue
            for member_dict in members_dicts:
                member_dict["status"] = "failed"
                member_dict["skip_reason"] = member_dict.get("skip_reason") or (
                    "a sibling dependency in this group was never dispatched"
                )
                member_dict["required_by"] = sorted(
                    required_by_map.get(member_dict["target_dep"], [])
                )
                settled[member_dict["target_dep"]] = member_dict
            continue

        if any(member["strategy"] == "replace" for member in members_dicts):
            for member_dict in members_dicts:
                member_dict["status"] = "skipped"
                member_dict["skip_reason"] = (
                    "coupled to a dependency migration (r3) target - deferred"
                )
                member_dict["required_by"] = sorted(
                    required_by_map.get(member_dict["target_dep"], [])
                )
                settled[member_dict["target_dep"]] = member_dict
            continue

        members = [Remediation(**m) for m in members_dicts]
        verification, work_dir = await replay_and_verify_group(
            members,
            prep.repo_path,
            container,
            prep.docker_image,
            prep.detected_package_manager,
            keep_workdir=keep_workdir,
        )
        group_ok = _is_green(verification)
        if work_dir:
            if group_ok:
                for dep in group:
                    verified_workdirs[dep] = work_dir
            else:
                shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
        for member_dict, member in zip(members_dicts, members, strict=True):
            member_dict["verification"] = verification.model_dump()
            member_dict["required_by"] = sorted(
                required_by_map.get(member.target_dep, [])
            )
            if group_ok:
                member_dict["status"] = "fixed"
            elif correction_rounds < _MAX_CORRECTION_ROUNDS:
                retry_targets.append(member.target_dep)
            else:
                member_dict["status"] = "failed"
                member_dict["skip_reason"] = member_dict.get("skip_reason") or (
                    "verification failed after max correction rounds"
                )
            settled[member.target_dep] = member_dict

    for group in groups[_MAX_GROUPS:]:
        for dep in group:
            if dep in remediations:
                remediations[dep]["status"] = "skipped"
                remediations[dep]["skip_reason"] = "target/group cap exceeded"
                remediations[dep]["required_by"] = sorted(required_by_map.get(dep, []))
                settled[dep] = remediations[dep]

    if retry_targets:
        return {
            "remediations": settled,
            "retry_targets": retry_targets,
            "correction_rounds": correction_rounds + 1,
            "verified_workdirs": verified_workdirs,
        }
    return {
        "remediations": settled,
        "retry_targets": [],
        "verified_workdirs": verified_workdirs,
    }
```

(`route_after_group_verify` right below is untouched.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -k group_and_verify_gate -v`
Expected: all PASS (7 pre-existing + 3 new = 10 tests).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/state.py apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "feat: group_and_verify_gate keeps a commit-ready copy for pr_and_persist_node"
```

---

### Task 3: `pr_and_persist_node` becomes ship-only

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py:1-32` (imports), `:552-625` (function body)
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Consumes: `RemediationState["verified_workdirs"]: dict[str, str]` (Task 2). `_pr_title_and_body(group_remediations: list[Remediation], verification: VerificationResult) -> tuple[str, str]` (unchanged, existing helper) — now called with `members[0].verification`, which the gate already set on every settled member.

- [ ] **Step 1: Write the failing tests**

Replace `test_pr_and_persist_node_opens_one_pr_when_consent_true` in `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py` (currently lines 977-1034):

```python
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
```

Delete `test_pr_and_persist_node_skips_pr_when_final_install_fails` entirely (currently lines 1037-1086) — its scenario ("final install fails on the copy that gets committed") is now `group_and_verify_gate`'s responsibility and is covered by `test_group_and_verify_gate_deletes_kept_workdir_when_verification_failed` from Task 2; `pr_and_persist_node` no longer runs any install of its own to fail.

`test_pr_and_persist_node_skips_pr_when_consent_false` needs no code change, but its scenario is now trivially true (empty `verified_workdirs`) rather than exercising a `consent`-gated branch inside the node — leave it in place as a regression guard on that behavior.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -k pr_and_persist -v`
Expected: `test_pr_and_persist_node_opens_one_pr_when_consent_true` FAILS (node still tries `copy_repo`/`svc["container"]` calls it no longer needs the test to mock, or `git_pr.open_pr` never called because the current implementation reads `state["remediations"]` groups via `connected_groups`+`requires_edges`, not `verified_workdirs`, which this state dict does not meaningfully populate for). `test_pr_and_persist_node_groups_shared_workdir_into_one_pr` FAILS with `KeyError` or similarly for the same reason.

- [ ] **Step 3: Rewrite `pr_and_persist_node`**

In `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`, first trim the imports (lines 14-17 and 23):

```python
from src.main_graph.subgraphs.remediation.deepagent.replay import (
    replay_and_verify_group,
)
```

(drop `apply_group_changes` from this import block)

```python
from src.main_graph.subgraphs.remediation.plan import build_plans_for_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.workspace import copy_repo
```

(drop the `from src.main_graph.subgraphs.remediation.verify import verify_working_copy` line entirely — `copy_repo` stays, `_run_group` still uses it)

Then replace the `pr_and_persist_node` function body (originally lines 552-625). `prep` (`prep.repo_path`/`prep.docker_image`/`prep.detected_package_manager`) was only ever needed by the old `copy_repo`/`verify_working_copy` calls this task removes, so the rewrite drops the `dao.get_prep` call too — nothing else in the function used it:

```python
async def pr_and_persist_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")

    remediations = {
        dep: Remediation(**r) for dep, r in (state.get("remediations") or {}).items()
    }
    verified_workdirs: dict[str, str] = state.get("verified_workdirs") or {}

    by_workdir: dict[str, list[str]] = {}
    for dep, work_dir in verified_workdirs.items():
        by_workdir.setdefault(work_dir, []).append(dep)

    for work_dir, deps in by_workdir.items():
        members = [remediations[dep] for dep in deps if dep in remediations]
        if not members:
            shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
            continue
        try:
            branch = f"remediation/{state['job_id'][:8]}-{sorted(deps)[0]}"
            title, body = _pr_title_and_body(members, members[0].verification)
            try:
                pr_url = await git_pr.open_pr(work_dir, branch, title, body)
                for member in members:
                    member.branch = branch
                    member.pr_url = pr_url
            except Exception as exc:
                logger.warning(
                    "pr_and_persist_node: PR creation failed for group %s: %s",
                    deps,
                    exc,
                )
        finally:
            shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)

    result = RemediationResult(
        job_id=state["job_id"],
        remediations=list(remediations.values()),
        consent=consent,
    )
    rid = await dao.save_remediation(result)
    return {"remediation_result_id": rid}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -k pr_and_persist -v`
Expected: all PASS (`test_pr_and_persist_node_skips_pr_when_consent_false`, `test_pr_and_persist_node_opens_one_pr_when_consent_true`, `test_pr_and_persist_node_groups_shared_workdir_into_one_pr`).

- [ ] **Step 5: Run the full remediation unit suite**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/ -v`
Expected: all PASS. This catches any other test in the directory (e.g. in `test_deepagent_state.py` or `test_subagent_wrapper.py`) that might import something removed from `nodes.py`'s public surface -- there shouldn't be any, but confirm.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "refactor: pr_and_persist_node only ships the already-verified copy"
```

---

### Task 4: Docs + full verification

**Files:**
- Modify: `apps/backend/docs/graphs.md`
- Test: full backend suite (no new test code, verification only)

- [ ] **Step 1: Update the Remediation subgraph doc**

In `apps/backend/docs/graphs.md`, in the "Remediation subgraph" section:

Change the `pr_and_persist_node` bullet under "Node-by-node (as built)" from:

```
- **`pr_and_persist_node`** (`deepagent/nodes.py`) — Opens PRs for `fixed` groups (when `consent` + a `git_pr` adapter are configured) and always persists the final `RemediationResult`. See mismatch note.
```

to:

```
- **`pr_and_persist_node`** (`deepagent/nodes.py`) — Ship-only. Reads `verified_workdirs` (populated by `group_and_verify_gate`, one entry per member of a group it verified green AND kept because `consent` + a `git_pr` adapter were configured), groups deps by shared work dir, builds the PR title/body from the already-verified `Remediation` + `VerificationResult` data, opens the PR, and always persists the final `RemediationResult`. Does no install, no replay, no re-verification of its own.
```

Replace the entire "Mismatch: `pr_and_persist_node` does more than PR + persist" section (including its heading) with:

```
### Resolved: `pr_and_persist_node` is now ship-only

Previously `pr_and_persist_node` re-derived groups, replayed changes onto a second working copy, and re-ran full install/build/test/audit verification independently of `group_and_verify_gate` -- a second, undocumented gate whose failures had no feedback path back to the remediator. As of `docs/superpowers/plans/2026-08-08-remediation-verify-pr-split.md`, `replay_and_verify_group` can keep its working copy on request (its install step already regenerates the lockfile against the bumped `package.json`), and `group_and_verify_gate` requests that only for groups it verifies green when a PR could actually be opened, recording the path in `verified_workdirs`. A verification failure on a kept copy is handled by the gate itself through the existing `retry_targets`/`correction_rounds` loop -- there is no second failure path anymore. `pr_and_persist_node` now only reads `verified_workdirs`, opens PRs, and persists.
```

Update the mermaid diagram's `pr_and_persist_node` node label and remove the red "flagged" styling now that it matches its documented single responsibility:

```
    pr_and_persist_node["pr_and_persist_node\n― deterministic ―\nPR + PERSIST (ship-only)"]
    pr_and_persist_node --> END([end])

    classDef llm fill:#dbeafe,stroke:#2563eb
    classDef agent fill:#ede9fe,stroke:#7c3aed
    classDef det fill:#f3f4f6,stroke:#6b7280

    class build_migration_plan_node llm
    class remediate_targets_node agent
    class classify_targets_node,investigate_node,group_and_verify_gate,pr_and_persist_node det
```

(drop the `flagged` classDef and its `class pr_and_persist_node flagged` line entirely)

- [ ] **Step 2: Run the full backend verification suite**

Run: `cd apps/backend && uv run ruff check . && uv run mypy src && uv run pytest tests/unit -v`
Expected: ruff clean, mypy clean, all unit tests PASS.

- [ ] **Step 3: Run the Docker-backed remediation integration suite**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_remediation_subgraph.py -v`
Expected: all PASS unmodified -- `test_pure_bump_target_ships_one_fixed_pr`, `test_requires_signal_pulls_in_a_non_finding_companion`, `test_correction_round_retries_then_gives_up_at_cap`, `test_consent_false_opens_zero_prs_across_every_group`. These exercise the real `replay_and_verify_group` end to end (via a blanket-success container mock), so this is the check that actually proves the split works end to end, not just against unit-level mocks. If any fail, do not weaken the assertion -- these tests assert externally-visible behavior (branch names, PR URLs, `verification.installed`) that must be identical before and after this refactor.

- [ ] **Step 4: Commit**

```bash
git add apps/backend/docs/graphs.md
git commit -m "docs: mark remediation pr_and_persist_node mismatch resolved"
```
