# Remediation v1 — Tier 0/1 Verified Dependency Bumps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a remediation phase between analysis and report that produces same-package version bumps, verifies them *together* in the sandbox (install → build → test + vuln re-audit), and opens one PR per job via the `gh` CLI — gated by a per-job consent flag.

**Architecture:** A new `remediation` subgraph runs after analysis, before report. It selects remediation targets deterministically (severity filter → anchor transitives to their direct dependent → unify findings sharing a direct-dep bump), then a self-correcting LLM orchestrator proposes same-package version bumps one action at a time, applying and verifying each over a single isolated working copy of the repo until the accumulated set verifies jointly. On consent, it opens one PR via a `GitPullRequestPort`/`GhCliAdapter`. LLM proposes the version; the sandbox verification gates it. Tier 2 (codemods) and Tier 3 (package replacement) cases are recorded as `skipped` breadcrumbs.

**Tech Stack:** Python 3.12+, LangGraph, Pydantic v2, pytest / pytest-asyncio, Docker (`node:lts-alpine` via `ContainerRunPort`), `gh` CLI, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-07-25-remediation-tier0-1-design.md`

## Global Constraints

- **Isolation:** remediation NEVER mutates `prep.repo_path`. It copies the repo (including `.git`) to a scratch dir, works there, and removes the scratch dir in a `finally`. The report subgraph runs after remediation against the pristine `prep.repo_path`.
- **Verification is the gate:** no PR is opened unless the accumulated working copy passes verification. The LLM proposes versions; a wrong pick fails verification and never ships.
- **Joint verification invariant:** verification always runs over the WHOLE working copy. A regression introduced by a later bump is feedback the orchestrator re-plans against; it never blind-reverts-and-continues.
- **Tier boundary:** v1 only ever writes `strategy="bump"`. A breaking-major requirement or a different-package recommendation is recorded as `status="skipped"` with a reason; NO code changes are attempted.
- **Consent:** writes (branch/push/PR) happen only when the per-job `remediate` flag is true. Without it, the stage still runs, records `Remediation` rows + patches, but `pr_url=None`, `consent=False`.
- **Severity floor:** reuse `settings.risk_min_severity`. Do NOT add a new config key.
- **Secrets:** never log a token. `gh` uses ambient `gh auth`; no token is threaded through this stage.
- **No changes to the report subgraph** in this plan. Report integration is deferred.
- Package-manager-aware throughout via `prep.detected_package_manager` (`npm` | `pnpm` | `yarn`).
- No emoji anywhere. Match existing module style (`from __future__ import annotations`, module-level `logger`, `get_llm(Model.GPT_5_4_MINI)`).

---

## File Structure

**New (production):**
- `src/models/remediation.py` — `VerificationResult`, `CodeChange`, `Remediation`, `RemediationResult`, `RemediationTarget`, `RemediationDecision`.
- `src/domain/ports/git_pr_port.py` — `GitPullRequestPort`.
- `src/main_graph/adapters/gh_cli_adapter.py` — `GhCliAdapter`.
- `src/main_graph/subgraphs/remediation/__init__.py` — exports `remediation_subgraph`.
- `src/main_graph/subgraphs/remediation/state.py` — `RemediationState`.
- `src/main_graph/subgraphs/remediation/graph.py` — `build_remediation_subgraph`.
- `src/main_graph/subgraphs/remediation/selection.py` — `select_remediation_targets`.
- `src/main_graph/subgraphs/remediation/workspace.py` — `copy_repo`, `apply_bump`, `working_copy_diff`, `pm_commands`.
- `src/main_graph/subgraphs/remediation/verify.py` — `verify_working_copy`.
- `src/main_graph/subgraphs/remediation/orchestrator.py` — `run_remediation`.
- `src/main_graph/subgraphs/remediation/nodes/__init__.py`, `nodes/remediate.py` — the node.

**Modified (production):**
- `src/db/result_dao.py` — `save_remediation`/`get_remediation` + `remediation_results` collection.
- `src/api/schemas.py` — `AnalysisRequest.remediate`.
- `src/api/routes.py` — pass `remediate` into `run_analysis`.
- `src/services/job_runner.py` — thread `remediate`; wire `REMEDIATION` artifact + finalize.
- `src/main_graph/config.py` — `PipelineConfigurable.remediate`, `git_pr`.
- `src/main_graph/constants.py` — `REMEDIATION`.
- `src/main_graph/state.py` — `MainState.remediation_result_id`.
- `src/main_graph/graph.py` — insert `REMEDIATION` between `ANALYSIS` and `REPORT`.

**New (tests):** one test module per production module under `tests/unit/...` (paths given per task).

---

## Task 1: Remediation data models

**Files:**
- Create: `src/models/remediation.py`
- Test: `tests/unit/models/test_remediation_models.py`

**Interfaces:**
- Produces: the models every later task consumes. Exact field names below are binding.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/models/test_remediation_models.py
from src.models.remediation import (
    CodeChange,
    Remediation,
    RemediationResult,
    RemediationTarget,
    VerificationResult,
)


def test_remediation_bump_defaults_are_tier0_1():
    r = Remediation(addresses=["lodash"], target_dep="lodash")
    assert r.strategy == "bump"
    assert r.status == "skipped"
    assert r.replacement_dep is None
    assert r.migration_plan == ""
    assert r.code_changes == []
    assert r.verification.installed is False
    assert r.verification.built is None
    assert r.attempts == 0
    assert r.patch == ""


def test_verification_partial_is_representable():
    v = VerificationResult(installed=True, built=None, tested=None, finding_resolved=True)
    assert v.built is None and v.tested is None and v.finding_resolved is True


def test_remediation_result_round_trip():
    res = RemediationResult(
        job_id="j1",
        remediations=[Remediation(addresses=["minimist"], target_dep="mkdirp",
                                   strategy="bump", from_range="^0.5.1", to_range="^0.5.5",
                                   status="fixed")],
        branch="remediation/j1", pr_url="https://gh/pr/1", consent=True,
    )
    doc = res.model_dump()
    assert RemediationResult(**doc).remediations[0].target_dep == "mkdirp"


def test_remediation_target_carries_addresses():
    t = RemediationTarget(target_dep="mkdirp", addresses=["minimist", "mkdirp"])
    assert t.addresses == ["minimist", "mkdirp"]


def test_code_change_shape():
    c = CodeChange(file="src/a.js", rationale="api moved")
    assert c.file == "src/a.js"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -q`
Expected: FAIL (module `src.models.remediation` does not exist).

- [ ] **Step 3: Implement the models**

```python
# src/models/remediation.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    installed: bool = False
    built: bool | None = None            # None = repo has no build script
    tested: bool | None = None           # None = repo has no test script
    finding_resolved: bool | None = None  # deterministic where checkable (vuln re-audit)
    logs_snippet: str = ""


class CodeChange(BaseModel):             # Tier 2/3 slot — empty in v1
    file: str
    rationale: str


class Remediation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    addresses: list[str]                 # analysis finding dep_names this covers
    target_dep: str                      # the DIRECT dep acted on (the anchor)
    strategy: Literal["bump", "bump_with_codemod", "replace"] = "bump"
    from_range: str | None = None
    to_range: str | None = None
    replacement_dep: str | None = None
    replacement_range: str | None = None
    migration_plan: str = ""
    code_changes: list[CodeChange] = Field(default_factory=list)
    status: Literal["fixed", "failed", "skipped"] = "skipped"
    skip_reason: str | None = None
    verification: VerificationResult = Field(default_factory=VerificationResult)
    attempts: int = 0
    patch: str = ""


class RemediationResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    remediations: list[Remediation] = Field(default_factory=list)
    branch: str | None = None
    pr_url: str | None = None
    consent: bool = False


class RemediationTarget(BaseModel):
    """Internal: a deduped unit of work produced by target selection."""
    target_dep: str                      # direct dep to bump
    addresses: list[str]                 # finding dep_names grouped under it
    current_range: str | None = None     # from package.json, if known


class RemediationDecision(BaseModel):
    """One orchestrator action (structured LLM output)."""
    action: Literal["bump", "skip", "finalize"]
    target_dep: str | None = None
    to_range: str | None = None
    skip_reason: str | None = None       # tier-2/3 or 'no fix' when action=skip
    reasoning: str = ""
```

- [ ] **Step 4: Run the tests, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/models/remediation.py tests/unit/models/test_remediation_models.py
git commit -m "feat(remediation): Tier 0/1/2/3-spanning data models"
```

---

## Task 2: Target selection (deterministic)

**Files:**
- Create: `src/main_graph/subgraphs/remediation/__init__.py` (empty for now), `src/main_graph/subgraphs/remediation/selection.py`
- Test: `tests/unit/subgraphs/remediation/__init__.py` (empty), `tests/unit/subgraphs/remediation/test_selection.py`

**Interfaces:**
- Consumes: `FindingNote` (`src.models.conductor`, fields `dep_name`/`severity`/`description`), the flat dependency graph dict (`{"direct": {name: version}, "packages": {...}}`), `is_direct`/`direct_dependents` (`src.main_graph.subgraphs.discovery.dependency_graph`), `filter_by_min_severity` + `settings.risk_min_severity`.
- Produces: `select_remediation_targets(findings: list[FindingNote], dependency_graph: dict, min_severity: str) -> list[RemediationTarget]`.

**Selection rules (binding):**
1. Filter findings to severity ≥ `min_severity` via `filter_by_min_severity`.
2. For each surviving finding, compute its anchor direct dep(s): if `is_direct(graph, dep_name)` → anchor is `[dep_name]`; else `direct_dependents(graph, dep_name)` (may be empty → the finding is un-anchorable and is dropped from remediation, since we have no direct lever).
3. Group findings by anchor direct dep. One `RemediationTarget` per distinct direct dep, `addresses` = sorted unique original finding `dep_name`s grouped under it, `current_range` = `graph["direct"].get(target_dep)`.
4. Deterministic order: targets sorted by `target_dep`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/subgraphs/remediation/test_selection.py
from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.models.conductor import FindingNote

GRAPH = {
    "direct": {"mkdirp": "0.5.1", "lodash": "4.17.11", "optimist": "0.6.1"},
    "packages": {
        "mkdirp@0.5.1": {"dependencies": ["minimist@0.0.8"]},
        "optimist@0.6.1": {"dependencies": ["minimist@0.0.8"]},
        "minimist@0.0.8": {"dependencies": []},
        "lodash@4.17.11": {"dependencies": []},
    },
}


def _f(dep, sev="high"):
    return FindingNote(dep_name=dep, severity=sev, description=f"{dep} issue", evidence=[])


def test_direct_finding_maps_to_itself():
    targets = select_remediation_targets([_f("lodash")], GRAPH, "high")
    assert [t.target_dep for t in targets] == ["lodash"]
    assert targets[0].addresses == ["lodash"]
    assert targets[0].current_range == "4.17.11"


def test_transitive_anchors_to_direct_parent():
    targets = select_remediation_targets([_f("minimist")], GRAPH, "high")
    # minimist is pulled by mkdirp AND optimist -> two targets, both addressing minimist
    assert sorted(t.target_dep for t in targets) == ["mkdirp", "optimist"]
    assert all(t.addresses == ["minimist"] for t in targets)


def test_two_transitives_under_same_direct_unify():
    graph = {
        "direct": {"parent": "1.0.0"},
        "packages": {
            "parent@1.0.0": {"dependencies": ["a@1", "b@1"]},
            "a@1": {"dependencies": []},
            "b@1": {"dependencies": []},
        },
    }
    targets = select_remediation_targets([_f("a"), _f("b")], graph, "high")
    assert len(targets) == 1
    assert targets[0].target_dep == "parent"
    assert targets[0].addresses == ["a", "b"]


def test_severity_filter_drops_below_floor():
    targets = select_remediation_targets([_f("lodash", "low")], GRAPH, "high")
    assert targets == []


def test_unanchorable_transitive_is_dropped():
    graph = {"direct": {"x": "1.0.0"}, "packages": {}}  # no edges to trace
    targets = select_remediation_targets([_f("ghost")], graph, "high")
    assert targets == []


def test_targets_sorted_by_dep():
    targets = select_remediation_targets([_f("optimist"), _f("lodash")], GRAPH, "high")
    assert [t.target_dep for t in targets] == ["lodash", "optimist"]
```

- [ ] **Step 2: Run, verify fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_selection.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# src/main_graph/subgraphs/remediation/selection.py
from __future__ import annotations

from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)
from src.models.conductor import FindingNote
from src.models.remediation import RemediationTarget
from src.utils.severity import filter_by_min_severity


def _anchors(graph: dict, dep_name: str) -> list[str]:
    if is_direct(graph, dep_name):
        return [dep_name]
    return direct_dependents(graph, dep_name)


def select_remediation_targets(
    findings: list[FindingNote], dependency_graph: dict, min_severity: str
) -> list[RemediationTarget]:
    """Deterministic: filter by severity, anchor transitives to their direct
    dependent(s), unify findings that share a direct-dep bump.

    Findings with no direct anchor (no lever the user controls) are dropped.
    """
    survivors = filter_by_min_severity(findings, min_severity)
    direct = dependency_graph.get("direct") or {}

    grouped: dict[str, set[str]] = {}
    for finding in survivors:
        for anchor in _anchors(dependency_graph, finding.dep_name):
            grouped.setdefault(anchor, set()).add(finding.dep_name)

    return [
        RemediationTarget(
            target_dep=dep,
            addresses=sorted(addressed),
            current_range=direct.get(dep),
        )
        for dep, addressed in sorted(grouped.items())
    ]
```

- [ ] **Step 4: Run, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_selection.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/remediation/__init__.py \
        src/main_graph/subgraphs/remediation/selection.py \
        tests/unit/subgraphs/remediation/__init__.py \
        tests/unit/subgraphs/remediation/test_selection.py
git commit -m "feat(remediation): deterministic target selection (filter, anchor, unify)"
```

---

## Task 3: Workspace helpers (copy, apply bump, diff, PM commands)

**Files:**
- Create: `src/main_graph/subgraphs/remediation/workspace.py`
- Test: `tests/unit/subgraphs/remediation/test_workspace.py`

**Interfaces:**
- Produces:
  - `copy_repo(src_repo_path: str) -> str` — copies the repo (incl. `.git`) to a fresh temp dir, returns its path.
  - `apply_bump(work_dir: str, target_dep: str, to_range: str) -> bool` — sets `dependencies`/`devDependencies[target_dep]` in `package.json` to `to_range`; returns False if the dep isn't declared there.
  - `working_copy_diff(work_dir: str) -> str` — `git -C work_dir diff` (unstaged working-tree changes) as a string.
  - `pm_commands(package_manager: str) -> dict[str, str]` — maps `{"install","build","test"}` to the PM-appropriate shell command.

**PM command map (binding):**
| PM | install | build | test |
|----|---------|-------|------|
| npm | `npm install` | `npm run build` | `npm test` |
| pnpm | `pnpm install --no-frozen-lockfile` | `pnpm run build` | `pnpm test` |
| yarn | `yarn install` | `yarn build` | `yarn test` |

Unknown PM → treat as `npm`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/subgraphs/remediation/test_workspace.py
import json
import os
import subprocess

import pytest

from src.main_graph.subgraphs.remediation.workspace import (
    apply_bump,
    copy_repo,
    pm_commands,
    working_copy_diff,
)


@pytest.fixture
def git_repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    (d / "package.json").write_text(json.dumps(
        {"name": "x", "dependencies": {"lodash": "^4.17.11"},
         "devDependencies": {"jest": "^29.0.0"}}))
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=d, check=True)
    return str(d)


def test_copy_repo_is_independent(git_repo):
    copy = copy_repo(git_repo)
    assert copy != git_repo
    assert os.path.isfile(os.path.join(copy, "package.json"))
    assert os.path.isdir(os.path.join(copy, ".git"))
    # mutating the copy does not touch the source
    apply_bump(copy, "lodash", "^4.17.21")
    src_pkg = json.load(open(os.path.join(git_repo, "package.json")))
    assert src_pkg["dependencies"]["lodash"] == "^4.17.11"


def test_apply_bump_dependencies(git_repo):
    assert apply_bump(git_repo, "lodash", "^4.17.21") is True
    pkg = json.load(open(os.path.join(git_repo, "package.json")))
    assert pkg["dependencies"]["lodash"] == "^4.17.21"


def test_apply_bump_devdependencies(git_repo):
    assert apply_bump(git_repo, "jest", "^29.7.0") is True
    pkg = json.load(open(os.path.join(git_repo, "package.json")))
    assert pkg["devDependencies"]["jest"] == "^29.7.0"


def test_apply_bump_undeclared_returns_false(git_repo):
    assert apply_bump(git_repo, "not-there", "^1.0.0") is False


def test_working_copy_diff_reflects_change(git_repo):
    apply_bump(git_repo, "lodash", "^4.17.21")
    diff = working_copy_diff(git_repo)
    assert "package.json" in diff and "4.17.21" in diff


def test_pm_commands_variants():
    assert pm_commands("pnpm")["install"] == "pnpm install --no-frozen-lockfile"
    assert pm_commands("yarn")["build"] == "yarn build"
    assert pm_commands("weird")["test"] == "npm test"  # fallback
```

- [ ] **Step 2: Run, verify fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_workspace.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# src/main_graph/subgraphs/remediation/workspace.py
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

_PM_COMMANDS: dict[str, dict[str, str]] = {
    "npm": {"install": "npm install", "build": "npm run build", "test": "npm test"},
    "pnpm": {
        "install": "pnpm install --no-frozen-lockfile",
        "build": "pnpm run build",
        "test": "pnpm test",
    },
    "yarn": {"install": "yarn install", "build": "yarn build", "test": "yarn test"},
}


def pm_commands(package_manager: str) -> dict[str, str]:
    return _PM_COMMANDS.get(package_manager, _PM_COMMANDS["npm"])


def copy_repo(src_repo_path: str) -> str:
    dst = tempfile.mkdtemp(prefix="remediation-")
    work = os.path.join(dst, "repo")
    shutil.copytree(src_repo_path, work, symlinks=True)
    return work


def apply_bump(work_dir: str, target_dep: str, to_range: str) -> bool:
    pkg_path = os.path.join(work_dir, "package.json")
    with open(pkg_path) as f:
        pkg = json.load(f)
    for section in ("dependencies", "devDependencies"):
        if target_dep in (pkg.get(section) or {}):
            pkg[section][target_dep] = to_range
            with open(pkg_path, "w") as f:
                json.dump(pkg, f, indent=2)
                f.write("\n")
            return True
    return False


async def working_copy_diff(work_dir: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", work_dir, "diff",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return out.decode(errors="replace")
```

Note: `working_copy_diff` is async (git subprocess). Update the test to `await` it and mark it `async` (the test above shows the sync intent; implement the test with `@pytest.mark.asyncio` and `diff = await working_copy_diff(git_repo)`).

- [ ] **Step 4: Adjust the diff test to async, run, verify pass**

Edit `test_working_copy_diff_reflects_change` to:
```python
import pytest


@pytest.mark.asyncio
async def test_working_copy_diff_reflects_change(git_repo):
    apply_bump(git_repo, "lodash", "^4.17.21")
    diff = await working_copy_diff(git_repo)
    assert "package.json" in diff and "4.17.21" in diff
```

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_workspace.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/remediation/workspace.py \
        tests/unit/subgraphs/remediation/test_workspace.py
git commit -m "feat(remediation): workspace helpers (copy, apply bump, diff, pm commands)"
```

---

## Task 4: Verification worker

**Files:**
- Create: `src/main_graph/subgraphs/remediation/verify.py`
- Test: `tests/unit/subgraphs/remediation/test_verify.py`

**Interfaces:**
- Consumes: `ContainerRunPort.run(image, command, volume, run_as_root, secret_env) -> (rc, stdout, stderr)`; `pm_commands`; `VerificationResult`.
- Produces:
  `async verify_working_copy(work_dir, container, docker_image, package_manager, targeted_deps) -> VerificationResult`

**Behavior (binding):**
- Read `package.json` scripts from `work_dir`. `has_build = "build" in scripts`. `has_test = "test" in scripts and scripts["test"].strip() != 'echo "Error: no test specified" && exit 1'`.
- Run inside ONE container invocation with a chained shell command, volume `f"{work_dir}:/workspace"`, `run_as_root=True`, image `docker_image`:
  1. `cd /workspace && <install>` — if rc != 0 → `installed=False`, stop, `logs_snippet=stderr`, return.
  2. `installed=True`. If `has_build`: run `<build>`; `built = (rc == 0)`; else `built=None`.
  3. If `has_test`: run `<test>`; `tested = (rc == 0)`; else `tested=None`.
  4. Re-audit: `<pm> audit --json`; parse; `finding_resolved = none of targeted_deps appear as vulnerable package keys`. If audit output isn't parseable → `finding_resolved=None`.
- For v1 keep it as SEPARATE `container.run` calls per step (simpler to test than shell chaining). Each step is its own `container.run`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/subgraphs/remediation/test_verify.py
import json

import pytest

from src.main_graph.subgraphs.remediation.verify import verify_working_copy


class FakeContainer:
    """Returns queued (rc, stdout, stderr) per run() call, in order."""
    def __init__(self, results):
        self._results = list(results)
        self.commands = []

    async def run(self, image, command, volume=None, run_as_root=False, secret_env=None):
        self.commands.append(command)
        return self._results.pop(0)


@pytest.fixture
def work_dir(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"build": "tsc", "test": "jest"},
         "dependencies": {"lodash": "^4.17.21"}}))
    return str(tmp_path)


@pytest.mark.asyncio
async def test_all_green_vuln_resolved(work_dir):
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, "", ""), (0, "", ""), (0, audit, "")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.installed and v.built and v.tested and v.finding_resolved is True


@pytest.mark.asyncio
async def test_install_failure_short_circuits(work_dir):
    c = FakeContainer([(1, "", "ENOENT")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.installed is False
    assert v.built is None and v.tested is None and v.finding_resolved is None
    assert "ENOENT" in v.logs_snippet
    assert len(c.commands) == 1  # stopped after install


@pytest.mark.asyncio
async def test_no_build_no_test_scripts(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "x", "dependencies": {"lodash": "^4.17.21"}}))
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, audit, "")])  # install, audit only
    v = await verify_working_copy(str(tmp_path), c, "node:lts-alpine", "npm", ["lodash"])
    assert v.installed and v.built is None and v.tested is None
    assert v.finding_resolved is True


@pytest.mark.asyncio
async def test_placeholder_test_script_is_skipped(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"test": 'echo "Error: no test specified" && exit 1'},
         "dependencies": {"lodash": "^4.17.21"}}))
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, audit, "")])
    v = await verify_working_copy(str(tmp_path), c, "node:lts-alpine", "npm", ["lodash"])
    assert v.tested is None


@pytest.mark.asyncio
async def test_finding_not_resolved_when_still_vulnerable(work_dir):
    audit = json.dumps({"vulnerabilities": {"lodash": {"severity": "high"}}})
    c = FakeContainer([(0, "", ""), (0, "", ""), (0, "", ""), (0, audit, "")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.finding_resolved is False


@pytest.mark.asyncio
async def test_test_failure_marks_tested_false(work_dir):
    audit = json.dumps({"vulnerabilities": {}})
    c = FakeContainer([(0, "", ""), (0, "", ""), (1, "", "1 failing"), (0, audit, "")])
    v = await verify_working_copy(work_dir, c, "node:lts-alpine", "npm", ["lodash"])
    assert v.built is True and v.tested is False
```

- [ ] **Step 2: Run, verify fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_verify.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# src/main_graph/subgraphs/remediation/verify.py
from __future__ import annotations

import json
import logging
import os

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.workspace import pm_commands
from src.models.remediation import VerificationResult

logger = logging.getLogger(__name__)

_PLACEHOLDER_TEST = 'echo "Error: no test specified" && exit 1'


def _scripts(work_dir: str) -> dict:
    try:
        with open(os.path.join(work_dir, "package.json")) as f:
            return json.load(f).get("scripts") or {}
    except Exception:
        return {}


def _audit_executable(package_manager: str) -> str:
    return package_manager if package_manager in ("pnpm", "yarn") else "npm"


def _resolved_from_audit(stdout: str, targeted_deps: list[str]) -> bool | None:
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    vulnerable = set((data.get("vulnerabilities") or {}).keys())
    return not any(dep in vulnerable for dep in targeted_deps)


async def verify_working_copy(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
    targeted_deps: list[str],
) -> VerificationResult:
    cmds = pm_commands(package_manager)
    volume = f"{work_dir}:/workspace"
    scripts = _scripts(work_dir)
    v = VerificationResult()

    async def _run(cmd: str) -> tuple[int, str, str]:
        return await container.run(
            image=docker_image,
            command=f"cd /workspace && {cmd}",
            volume=volume,
            run_as_root=True,
        )

    rc, _out, err = await _run(cmds["install"])
    if rc != 0:
        v.logs_snippet = err[:1000]
        return v
    v.installed = True

    if "build" in scripts:
        rc, _out, err = await _run(cmds["build"])
        v.built = rc == 0
        if rc != 0:
            v.logs_snippet = err[:1000]

    test_script = (scripts.get("test") or "").strip()
    if test_script and test_script != _PLACEHOLDER_TEST:
        rc, _out, err = await _run(cmds["test"])
        v.tested = rc == 0
        if rc != 0:
            v.logs_snippet = err[:1000]

    _rc, out, _err = await _run(f"{_audit_executable(package_manager)} audit --json")
    v.finding_resolved = _resolved_from_audit(out, targeted_deps)
    return v
```

- [ ] **Step 4: Run, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_verify.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/remediation/verify.py \
        tests/unit/subgraphs/remediation/test_verify.py
git commit -m "feat(remediation): container-backed verification worker"
```

---

## Task 5: Git/PR port + gh CLI adapter

**Files:**
- Create: `src/domain/ports/git_pr_port.py`, `src/main_graph/adapters/gh_cli_adapter.py`
- Test: `tests/unit/adapters/test_gh_cli_adapter.py`

**Interfaces:**
- Produces:
  ```python
  class GitPullRequestPort(ABC):
      @abstractmethod
      async def open_pr(self, work_dir: str, branch: str, title: str, body: str) -> str: ...
  ```
  Returns the PR URL. `GhCliAdapter` runs, in `work_dir`: `git checkout -b <branch>`, `git add -A`, `git -c user.email/name commit -m <title>`, `git push -u origin <branch>`, `gh pr create --title <title> --body <body> --head <branch>` and returns the URL `gh` prints (last non-empty stdout line).

**Binding:** each git/gh step is a separate `asyncio.create_subprocess_exec` call with args as a list (never a shell string — no injection surface). On any non-zero rc, raise `RuntimeError` with the step's stderr.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/adapters/test_gh_cli_adapter.py
import pytest

from src.main_graph.adapters.gh_cli_adapter import GhCliAdapter


class FakeProc:
    def __init__(self, rc, out=b"", err=b""):
        self.returncode = rc
        self._out, self._err = out, err

    async def communicate(self):
        return self._out, self._err


@pytest.mark.asyncio
async def test_open_pr_runs_steps_and_returns_url(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if args[0] == "gh":
            return FakeProc(0, out=b"https://github.com/o/r/pull/7\n")
        return FakeProc(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    url = await GhCliAdapter().open_pr("/w", "remediation/j1", "Fix deps", "body")
    assert url == "https://github.com/o/r/pull/7"
    programs = [c[0] for c in calls]
    assert programs.count("git") >= 4 and programs[-1] == "gh"


@pytest.mark.asyncio
async def test_open_pr_raises_on_git_failure(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProc(1, err=b"branch exists")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="branch exists"):
        await GhCliAdapter().open_pr("/w", "b", "t", "b")
```

- [ ] **Step 2: Run, verify fail**

Run: `cd apps/backend && uv run pytest tests/unit/adapters/test_gh_cli_adapter.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# src/domain/ports/git_pr_port.py
from __future__ import annotations

from abc import ABC, abstractmethod


class GitPullRequestPort(ABC):
    @abstractmethod
    async def open_pr(
        self, work_dir: str, branch: str, title: str, body: str
    ) -> str:
        """Create a branch, commit the working tree, push, open a PR.
        Returns the PR URL. Raises RuntimeError on any git/gh failure."""
```

```python
# src/main_graph/adapters/gh_cli_adapter.py
from __future__ import annotations

import asyncio
import logging

from src.domain.ports.git_pr_port import GitPullRequestPort

logger = logging.getLogger(__name__)


class GhCliAdapter(GitPullRequestPort):
    async def _run(self, *args: str, cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode(errors="replace").strip() or f"{args[0]} failed")
        return out.decode(errors="replace")

    async def open_pr(
        self, work_dir: str, branch: str, title: str, body: str
    ) -> str:
        await self._run("git", "checkout", "-b", branch, cwd=work_dir)
        await self._run("git", "add", "-A", cwd=work_dir)
        await self._run(
            "git", "-c", "user.email=remediation@misi", "-c", "user.name=misi-remediation",
            "commit", "-m", title, cwd=work_dir,
        )
        await self._run("git", "push", "-u", "origin", branch, cwd=work_dir)
        out = await self._run(
            "gh", "pr", "create", "--title", title, "--body", body, "--head", branch,
            cwd=work_dir,
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1] if lines else ""
```

- [ ] **Step 4: Run, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/adapters/test_gh_cli_adapter.py -q`
Expected: PASS (2 tests). Create `tests/unit/adapters/__init__.py` if missing.

- [ ] **Step 5: Commit**

```bash
git add src/domain/ports/git_pr_port.py src/main_graph/adapters/gh_cli_adapter.py \
        tests/unit/adapters/test_gh_cli_adapter.py tests/unit/adapters/__init__.py
git commit -m "feat(remediation): GitPullRequestPort + gh CLI adapter"
```

---

## Task 6: The remediation orchestrator (self-correcting loop)

**Files:**
- Create: `src/main_graph/subgraphs/remediation/orchestrator.py`
- Test: `tests/unit/subgraphs/remediation/test_orchestrator.py`

**Interfaces:**
- Consumes: `RemediationTarget`, `RemediationDecision`, `Remediation`, `VerificationResult`; `select_remediation_targets` (Task 2) is called by the NODE, not here — the orchestrator receives targets. It receives a `verify` callable and an `apply` callable (dependency-injected so tests mock them), plus `evidence` (audit + outdated dicts) and `get_llm` decision function.
- Produces:
  ```python
  async def run_remediation(
      targets: list[RemediationTarget],
      work_dir: str,
      evidence: dict,                      # {"audit": {...}, "outdated": {...}}
      apply_bump: Callable[[str, str, str], bool],   # (work_dir, dep, range)->bool
      verify: Callable[[list[str]], Awaitable[VerificationResult]],  # (targeted_deps)->result
      diff: Callable[[], Awaitable[str]],  # working_copy_diff bound to work_dir
      decide: Callable[[str], Awaitable[RemediationDecision]] | None = None,
      max_iterations: int = 8,
  ) -> list[Remediation]
  ```

**Loop (binding):**
- Maintain `remediations: dict[str, Remediation]` keyed by `target_dep`, initialized one per target with `status="skipped"`, `skip_reason="not attempted"`, `addresses` from the target.
- `applied_deps: set[str]` — deps currently applied to the working copy.
- For up to `max_iterations`:
  - Build the decision prompt (targets, current per-target status, `applied_deps`, last verification summary, `evidence`).
  - `decision = await decide(prompt)`.
  - `action == "finalize"` → break.
  - `action == "skip"` → set that target `status="skipped"`, `skip_reason=decision.skip_reason or "no fix"`, `strategy` inferred: `"replace"` if reason mentions "package"/"replace", `"bump_with_codemod"` if reason mentions "major"/"migration", else `"bump"`. Continue.
  - `action == "bump"` → `apply_bump(work_dir, target_dep, to_range)`; if False → mark that target `failed` ("dep not declared"); else set from/to ranges, `attempts += 1`, `applied_deps.add(target_dep)`, then `v = await verify(sorted(applied_deps ∪ addresses of applied targets))`. Store `v` on the target. If `v` is green (installed and built is not False and tested is not False and finding_resolved is not False) → mark ALL currently-applied targets `status="fixed"`; else feed the failure back (loop continues) — the NEXT decision may adjust/skip; leave statuses as-is but keep `applied_deps` (the orchestrator decides whether to re-bump or skip).
- After the loop: for every target still not `fixed`, if it was applied but never verified green, set `status="failed"`. Compute `patch` for each `fixed` target from `await diff()` (v1: attach the full working-copy diff to each fixed remediation — a per-dep split is a later refinement; document this).
- "Green" helper: `installed and built is not False and tested is not False and finding_resolved is not False`.

The default `decide` (when None) builds a `RemediationDecision` via `get_llm(Model.GPT_5_4_MINI).with_structured_output(RemediationDecision, method="function_calling")` — tests always inject `decide`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/subgraphs/remediation/test_orchestrator.py
import pytest

from src.main_graph.subgraphs.remediation.orchestrator import run_remediation
from src.models.remediation import RemediationDecision, RemediationTarget, VerificationResult

GREEN = VerificationResult(installed=True, built=True, tested=True, finding_resolved=True)
REDTEST = VerificationResult(installed=True, built=True, tested=False, finding_resolved=True)


def _target(dep, addresses=None):
    return RemediationTarget(target_dep=dep, addresses=addresses or [dep], current_range="^1.0.0")


def _scripted_decider(actions):
    seq = iter(actions)

    async def decide(_prompt):
        return next(seq)
    return decide


def _apply_ok(work_dir, dep, rng):
    return True


@pytest.mark.asyncio
async def test_single_bump_verifies_and_fixes():
    verified = []

    async def verify(targeted):
        verified.append(targeted)
        return GREEN

    async def diff():
        return "PATCH"

    decide = _scripted_decider([
        RemediationDecision(action="bump", target_dep="lodash", to_range="^4.17.21"),
        RemediationDecision(action="finalize"),
    ])
    out = await run_remediation([_target("lodash")], "/w", {}, _apply_ok, verify, diff,
                                decide=decide)
    r = {x.target_dep: x for x in out}["lodash"]
    assert r.status == "fixed" and r.to_range == "^4.17.21" and r.patch == "PATCH"
    assert r.attempts == 1


@pytest.mark.asyncio
async def test_failed_verification_then_adjust_succeeds():
    results = iter([REDTEST, GREEN])

    async def verify(targeted):
        return next(results)

    async def diff():
        return "PATCH"

    decide = _scripted_decider([
        RemediationDecision(action="bump", target_dep="lodash", to_range="^4.17.20"),
        RemediationDecision(action="bump", target_dep="lodash", to_range="^4.17.21"),
        RemediationDecision(action="finalize"),
    ])
    out = await run_remediation([_target("lodash")], "/w", {}, _apply_ok, verify, diff,
                                decide=decide)
    r = out[0]
    assert r.status == "fixed" and r.to_range == "^4.17.21" and r.attempts == 2


@pytest.mark.asyncio
async def test_skip_records_tier2_breadcrumb():
    async def verify(targeted):
        return GREEN

    async def diff():
        return ""

    decide = _scripted_decider([
        RemediationDecision(action="skip", target_dep="chalk",
                            skip_reason="needs major (Tier 2)"),
        RemediationDecision(action="finalize"),
    ])
    out = await run_remediation([_target("chalk")], "/w", {}, _apply_ok, verify, diff,
                                decide=decide)
    r = out[0]
    assert r.status == "skipped" and r.strategy == "bump_with_codemod"


@pytest.mark.asyncio
async def test_undeclared_dep_marks_failed():
    async def verify(targeted):
        return GREEN

    async def diff():
        return ""

    def apply_false(w, d, r):
        return False

    decide = _scripted_decider([
        RemediationDecision(action="bump", target_dep="ghost", to_range="^9"),
        RemediationDecision(action="finalize"),
    ])
    out = await run_remediation([_target("ghost")], "/w", {}, apply_false, verify, diff,
                                decide=decide)
    assert out[0].status == "failed"


@pytest.mark.asyncio
async def test_bounded_iterations_marks_unresolved_failed():
    async def verify(targeted):
        return REDTEST  # never green

    async def diff():
        return ""

    async def always_bump(_prompt):
        return RemediationDecision(action="bump", target_dep="lodash", to_range="^4.17.21")

    out = await run_remediation([_target("lodash")], "/w", {}, _apply_ok, verify, diff,
                                decide=always_bump, max_iterations=3)
    assert out[0].status == "failed" and out[0].attempts >= 1
```

- [ ] **Step 2: Run, verify fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_orchestrator.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# src/main_graph/subgraphs/remediation/orchestrator.py
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from src.main_graph.subgraphs.remediation.workspace import working_copy_diff
from src.models.remediation import (
    Remediation,
    RemediationDecision,
    RemediationTarget,
    VerificationResult,
)
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You remediate Node.js dependency risks by proposing SAME-PACKAGE version bumps.
For each target (a direct dependency), propose a to_range that resolves the
findings it addresses, grounded in the audit fix path and outdated data below.

Rules:
- Only bumps. If the fix needs a breaking MAJOR upgrade or code changes, action
  "skip" with skip_reason "needs major (Tier 2)". If the recommendation is a
  DIFFERENT package, action "skip" with skip_reason "different package (Tier 3)".
- One action per turn. After a bump, you will see the whole-copy verification
  result; if it regressed, adjust a range or skip a target so the set verifies
  TOGETHER.
- action "finalize" when every target is fixed or skipped.

Evidence:
{evidence}

Targets and current status:
{status}

Applied bumps: {applied}
Last verification: {last_verification}
"""


def _infer_strategy(reason: str) -> str:
    low = (reason or "").lower()
    if "package" in low or "replace" in low:
        return "replace"
    if "major" in low or "migration" in low or "codemod" in low:
        return "bump_with_codemod"
    return "bump"


def _is_green(v: VerificationResult) -> bool:
    return (
        v.installed
        and v.built is not False
        and v.tested is not False
        and v.finding_resolved is not False
    )


def _default_decider() -> Callable[[str], Awaitable[RemediationDecision]]:
    structured = _llm.with_structured_output(
        RemediationDecision, method="function_calling"
    )

    async def decide(prompt: str) -> RemediationDecision:
        return await structured.ainvoke([{"role": "user", "content": prompt}])

    return decide


async def run_remediation(
    targets: list[RemediationTarget],
    work_dir: str,
    evidence: dict,
    apply_bump: Callable[[str, str, str], bool],
    verify: Callable[[list[str]], Awaitable[VerificationResult]],
    diff: Callable[[], Awaitable[str]] | None = None,
    decide: Callable[[str], Awaitable[RemediationDecision]] | None = None,
    max_iterations: int = 8,
) -> list[Remediation]:
    decide = decide or _default_decider()
    if diff is None:
        async def diff() -> str:  # noqa: E306
            return await working_copy_diff(work_dir)

    rem: dict[str, Remediation] = {
        t.target_dep: Remediation(
            addresses=t.addresses, target_dep=t.target_dep,
            from_range=t.current_range, status="skipped", skip_reason="not attempted",
        )
        for t in targets
    }
    by_dep = {t.target_dep: t for t in targets}
    applied: set[str] = set()
    last_v: VerificationResult | None = None

    for _ in range(max_iterations):
        prompt = _SYSTEM.format(
            evidence=json.dumps(evidence)[:4000],
            status=json.dumps({d: r.status for d, r in rem.items()}),
            applied=sorted(applied),
            last_verification=(last_v.model_dump() if last_v else "none"),
        )
        decision = await decide(prompt)

        if decision.action == "finalize":
            break

        dep = decision.target_dep or ""
        if dep not in rem:
            continue

        if decision.action == "skip":
            rem[dep].status = "skipped"
            rem[dep].skip_reason = decision.skip_reason or "no fix"
            rem[dep].strategy = _infer_strategy(rem[dep].skip_reason)
            applied.discard(dep)
            continue

        # action == "bump"
        if not apply_bump(work_dir, dep, decision.to_range or ""):
            rem[dep].status = "failed"
            rem[dep].skip_reason = "dependency not declared in package.json"
            continue
        rem[dep].strategy = "bump"
        rem[dep].to_range = decision.to_range
        rem[dep].attempts += 1
        applied.add(dep)

        targeted = sorted(
            {d for d in applied}
            | {a for d in applied for a in by_dep[d].addresses}
        )
        last_v = await verify(targeted)
        for d in applied:
            rem[d].verification = last_v

        if _is_green(last_v):
            for d in applied:
                rem[d].status = "fixed"

    # finalize statuses + patches
    patch = await diff() if any(r.status == "fixed" for r in rem.values()) else ""
    for d, r in rem.items():
        if d in applied and r.status != "fixed":
            r.status = "failed"
        if r.status == "fixed":
            r.patch = patch
    return list(rem.values())
```

- [ ] **Step 4: Run, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_orchestrator.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/remediation/orchestrator.py \
        tests/unit/subgraphs/remediation/test_orchestrator.py
git commit -m "feat(remediation): self-correcting orchestrator loop"
```

---

## Task 7: Consent flag — request schema + config threading

**Files:**
- Modify: `src/api/schemas.py`, `src/api/routes.py`, `src/services/job_runner.py`, `src/main_graph/config.py`
- Test: extend `tests/unit/test_routes.py`, `tests/unit/services/test_job_runner.py`

**Interfaces:**
- Produces: `configurable["remediate"]: bool` and `configurable["git_pr"]: GitPullRequestPort`, read by the node (Task 8).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_routes.py` (mirror the existing `used_pat` test style):
```python
def test_analyze_threads_remediate_flag(monkeypatch):
    captured = {}

    async def fake_run_analysis(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("src.api.routes.run_analysis", fake_run_analysis)
    # ... build AnalysisRequest(repo_url=..., concern=..., remediate=True),
    #     call analyze() with a fake dao, await the created task,
    #     assert captured["remediate"] is True
```

Add to `tests/unit/services/test_job_runner.py` (mirror the `github_token` config tests):
```python
def test_build_config_sets_remediate_and_git_pr():
    from src.services.job_runner import _build_config
    cfg = _build_config("j1", dao=object(), cost_cb=_noop_cb(), remediate=True)
    assert cfg["configurable"]["remediate"] is True
    assert cfg["configurable"]["git_pr"] is not None


def test_build_config_remediate_defaults_false():
    from src.services.job_runner import _build_config
    cfg = _build_config("j1", dao=object(), cost_cb=_noop_cb())
    assert cfg["configurable"]["remediate"] is False
```
(Reuse whatever `cost_cb`/dao stand-ins the existing tests in that file use.)

- [ ] **Step 2: Run, verify fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_routes.py tests/unit/services/test_job_runner.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the edits**

`src/api/schemas.py` — add to `AnalysisRequest`:
```python
    remediate: bool = False
```

`src/api/routes.py` — in `analyze()`, pass it into `run_analysis(...)`:
```python
            autopilot=request.autopilot,
            dao=dao,
            github_token=request.github_token,
            remediate=request.remediate,
```

`src/main_graph/config.py` — add to `PipelineConfigurable` and imports:
```python
from src.domain.ports.git_pr_port import GitPullRequestPort
...
    # Remediation write consent (Workstream C / D3-lite). Absent => no writes.
    remediate: NotRequired[bool]
    git_pr: NotRequired[GitPullRequestPort]
```

`src/services/job_runner.py` — extend `_build_config` and `run_analysis`:
```python
from src.main_graph.adapters.gh_cli_adapter import GhCliAdapter
...
def _build_config(
    job_id: str,
    dao: JobRepositoryPort,
    cost_cb: CostCallback,
    github_token: str | None = None,
    remediate: bool = False,
) -> dict:
    container = DockerContainerAdapter()
    configurable = {
        "thread_id": job_id,
        "job_repo": dao,
        "container": container,
        "docker_tool": make_docker_tool(container),
        "result_dao": get_result_dao(),
        "input_cache": get_input_cache(),
        "remediate": remediate,
        "git_pr": GhCliAdapter(),
    }
    if github_token:
        configurable["github_token"] = github_token
    return {"configurable": configurable, "callbacks": [cost_cb]}
```
```python
async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    autopilot: bool,
    dao: JobRepositoryPort,
    github_token: str | None = None,
    remediate: bool = False,
) -> None:
    ...
    config = _build_config(job_id, dao, cost_cb, github_token=github_token,
                           remediate=remediate)
```

- [ ] **Step 4: Run, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_routes.py tests/unit/services/test_job_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/schemas.py src/api/routes.py src/main_graph/config.py \
        src/services/job_runner.py tests/unit/test_routes.py \
        tests/unit/services/test_job_runner.py
git commit -m "feat(remediation): remediate consent flag + config threading"
```

---

## Task 8: DAO persistence + remediation node + subgraph

**Files:**
- Modify: `src/db/result_dao.py`
- Create: `src/main_graph/subgraphs/remediation/state.py`, `.../nodes/__init__.py`, `.../nodes/remediate.py`, `.../graph.py`, update `.../__init__.py`
- Test: `tests/unit/subgraphs/remediation/test_remediate_node.py`

**Interfaces:**
- Consumes: `select_remediation_targets`, `run_remediation`, `verify_working_copy`, `copy_repo`/`apply_bump`/`working_copy_diff`, `GitPullRequestPort`, `npm_audit`/`npm_outdated` tools, `get_services`, `ResultDAO`.
- Produces: node `remediate(state, config) -> {"remediation_result_id": str}`; `remediation_subgraph` compiled graph.

**`RemediationState`:**
```python
class RemediationState(TypedDict):
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str
    remediation_result_id: NotRequired[str]
```

**Node behavior (binding):**
1. `svc = get_services(config)`; `dao = svc["result_dao"]`; `container = svc["container"]`; `consent = bool(svc.get("remediate"))`; `git_pr = svc.get("git_pr")`.
2. `analysis = await dao.get_analysis(state["analysis_result_id"])`; `prep = await dao.get_prep(state["prep_result_id"])`.
3. `targets = select_remediation_targets(analysis.findings, prep.dependency_graph, settings.risk_min_severity)`.
4. If no targets → persist empty `RemediationResult`, return its id.
5. `work_dir = copy_repo(prep.repo_path)` (in a `try/finally` that `shutil.rmtree`s the temp parent).
6. Gather evidence: `audit = await npm_audit(prep.repo_path, container, prep.docker_image, prep.detected_package_manager)`; `outdated = await npm_outdated(prep.repo_path, container, prep.docker_image)`. (Run against the pristine path — read-only.)
7. Bind `verify = lambda deps: verify_working_copy(work_dir, container, prep.docker_image, prep.detected_package_manager, deps)`; `apply = lambda w, d, r: apply_bump(w, d, r)`; `diff = lambda: working_copy_diff(work_dir)`.
8. `remediations = await run_remediation(targets, work_dir, {"audit": audit, "outdated": outdated}, apply, verify, diff)`.
9. Build `RemediationResult(job_id=..., remediations=..., consent=consent)`.
10. If `consent` and `git_pr` and any `status=="fixed"`: `branch = f"remediation/{job_id[:8]}"`; `title/body` summarizing fixed remediations; `pr_url = await git_pr.open_pr(work_dir, branch, title, body)`; set `result.branch/pr_url`. Wrap in try/except → on failure log, leave `pr_url=None`.
11. `rid = await dao.save_remediation(result)`; return `{"remediation_result_id": rid}`.

- [ ] **Step 1: DAO — add persistence + failing test**

`src/db/result_dao.py`:
```python
from src.models.results import AnalysisResult, EvidenceBundle, PrepResult, ReportResult
from src.models.remediation import RemediationResult
...
        self._remediation = db["remediation_results"]
...
    async def save_remediation(self, result: RemediationResult) -> str:
        await self._remediation.insert_one(result.model_dump())
        return result.id

    async def get_remediation(self, result_id: str) -> RemediationResult:
        doc = await self._remediation.find_one({"id": result_id}, {"_id": 0})
        if doc is None:
            raise LookupError(f"RemediationResult not found: {result_id}")
        return RemediationResult(**doc)
```

- [ ] **Step 2: Write the node's failing test**

```python
# tests/unit/subgraphs/remediation/test_remediate_node.py
import pytest

from src.main_graph.subgraphs.remediation.nodes.remediate import remediate
from src.models.conductor import FindingNote
from src.models.remediation import Remediation
from src.models.results import AnalysisResult, PrepResult


class FakeDao:
    def __init__(self, analysis, prep):
        self._a, self._p = analysis, prep
        self.saved = None

    async def get_analysis(self, _id):
        return self._a

    async def get_prep(self, _id):
        return self._p

    async def save_remediation(self, result):
        self.saved = result
        return "rem-1"


def _prep():
    return PrepResult(
        job_id="j1", repo_path="/tmp/does-not-matter", project_metadata={},
        manifest_files=["package.json"], detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.11"}, "packages": {}},
        discovery_summary="", vector_store_id="",
    )


def _analysis():
    return AnalysisResult(
        job_id="j1", concern="c",
        findings=[FindingNote(dep_name="lodash", severity="high", description="cve", evidence=[])],
        evidence_bundle_ids=[], iteration_count=1,
    )


@pytest.mark.asyncio
async def test_node_persists_result_and_skips_pr_without_consent(monkeypatch):
    dao = FakeDao(_analysis(), _prep())
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.copy_repo",
        lambda p: "/tmp/work")
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.shutil.rmtree",
        lambda *a, **k: None)
    async def fake_audit(*a, **k):
        return {"vulnerabilities": {}}
    async def fake_outdated(*a, **k):
        return {"outdated": {}}
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_audit", fake_audit)
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_outdated", fake_outdated)
    async def fake_run(*a, **k):
        return [Remediation(addresses=["lodash"], target_dep="lodash",
                            strategy="bump", to_range="^4.17.21", status="fixed", patch="P")]
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.run_remediation", fake_run)

    config = {"configurable": {"result_dao": dao, "container": object(),
                               "remediate": False, "git_pr": None}}
    out = await remediate(
        {"job_id": "j1", "concern": "c", "prep_result_id": "p", "analysis_result_id": "a"},
        config)
    assert out["remediation_result_id"] == "rem-1"
    assert dao.saved.consent is False and dao.saved.pr_url is None
    assert dao.saved.remediations[0].status == "fixed"


@pytest.mark.asyncio
async def test_node_opens_pr_with_consent(monkeypatch):
    dao = FakeDao(_analysis(), _prep())
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.copy_repo",
        lambda p: "/tmp/work")
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.shutil.rmtree",
        lambda *a, **k: None)
    async def fake_audit(*a, **k):
        return {}
    async def fake_outdated(*a, **k):
        return {}
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_audit", fake_audit)
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.npm_outdated", fake_outdated)
    async def fake_run(*a, **k):
        return [Remediation(addresses=["lodash"], target_dep="lodash",
                            strategy="bump", to_range="^4.17.21", status="fixed", patch="P")]
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.nodes.remediate.run_remediation", fake_run)

    class FakePR:
        async def open_pr(self, work_dir, branch, title, body):
            self.branch = branch
            return "https://gh/pull/9"

    pr = FakePR()
    config = {"configurable": {"result_dao": dao, "container": object(),
                               "remediate": True, "git_pr": pr}}
    await remediate({"job_id": "job12345", "concern": "c",
                     "prep_result_id": "p", "analysis_result_id": "a"}, config)
    assert dao.saved.pr_url == "https://gh/pull/9"
    assert dao.saved.consent is True and pr.branch.startswith("remediation/")
```

- [ ] **Step 3: Implement node + state + graph + subgraph export**

```python
# src/main_graph/subgraphs/remediation/state.py
from __future__ import annotations

from typing import NotRequired

from typing_extensions import TypedDict


class RemediationState(TypedDict):
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str
    remediation_result_id: NotRequired[str]
```

```python
# src/main_graph/subgraphs/remediation/nodes/remediate.py
from __future__ import annotations

import logging
import os
import shutil

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.orchestrator import run_remediation
from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import (
    apply_bump,
    copy_repo,
    working_copy_diff,
)
from src.main_graph.tools.npm_cli import npm_audit, npm_outdated
from src.models.remediation import RemediationResult
from src.utils.config import settings

logger = logging.getLogger(__name__)


def _pr_body(remediations) -> tuple[str, str]:
    fixed = [r for r in remediations if r.status == "fixed"]
    title = f"Remediate {len(fixed)} dependency finding(s)"
    lines = ["Automated dependency remediation (verified in sandbox):", ""]
    for r in fixed:
        note = []
        if r.verification.tested is None:
            note.append("no tests in repo")
        if r.verification.finding_resolved is None:
            note.append("resolution not deterministically checkable")
        suffix = f" ({'; '.join(note)})" if note else ""
        lines.append(
            f"- {r.target_dep} {r.from_range} -> {r.to_range} "
            f"(fixes: {', '.join(r.addresses)}){suffix}"
        )
    return title, "\n".join(lines)


async def remediate(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")

    analysis = await dao.get_analysis(state["analysis_result_id"])
    prep = await dao.get_prep(state["prep_result_id"])

    targets = select_remediation_targets(
        analysis.findings, prep.dependency_graph, settings.risk_min_severity
    )
    logger.info("remediate: %d target(s) after selection", len(targets))

    if not targets:
        rid = await dao.save_remediation(
            RemediationResult(job_id=state["job_id"], consent=consent)
        )
        return {"remediation_result_id": rid}

    work_dir = copy_repo(prep.repo_path)
    try:
        audit = await npm_audit(
            prep.repo_path, container, prep.docker_image, prep.detected_package_manager
        )
        outdated = await npm_outdated(prep.repo_path, container, prep.docker_image)

        async def verify(deps):
            return await verify_working_copy(
                work_dir, container, prep.docker_image,
                prep.detected_package_manager, deps,
            )

        async def diff():
            return await working_copy_diff(work_dir)

        remediations = await run_remediation(
            targets, work_dir, {"audit": audit, "outdated": outdated},
            apply_bump, verify, diff,
        )

        result = RemediationResult(
            job_id=state["job_id"], remediations=remediations, consent=consent
        )

        if consent and git_pr and any(r.status == "fixed" for r in remediations):
            branch = f"remediation/{state['job_id'][:8]}"
            title, body = _pr_body(remediations)
            try:
                result.pr_url = await git_pr.open_pr(work_dir, branch, title, body)
                result.branch = branch
            except Exception as exc:
                logger.warning("remediate: PR creation failed: %s", exc)

        rid = await dao.save_remediation(result)
        return {"remediation_result_id": rid}
    finally:
        shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
```

```python
# src/main_graph/subgraphs/remediation/nodes/__init__.py
from src.main_graph.subgraphs.remediation.nodes.remediate import remediate

__all__ = ["remediate"]
```

```python
# src/main_graph/subgraphs/remediation/graph.py
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.remediation.nodes import remediate
from src.main_graph.subgraphs.remediation.state import RemediationState


def build_remediation_subgraph():
    builder = StateGraph(RemediationState)
    builder.add_node("remediate", remediate)
    builder.add_edge(START, "remediate")
    builder.add_edge("remediate", END)
    return builder.compile()
```

```python
# src/main_graph/subgraphs/remediation/__init__.py
from src.main_graph.subgraphs.remediation.graph import build_remediation_subgraph

remediation_subgraph = build_remediation_subgraph()

__all__ = ["remediation_subgraph"]
```

- [ ] **Step 4: Run, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_remediate_node.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/db/result_dao.py src/main_graph/subgraphs/remediation/ \
        tests/unit/subgraphs/remediation/test_remediate_node.py
git commit -m "feat(remediation): DAO persistence + remediation node & subgraph"
```

---

## Task 9: Main graph wiring + job_runner artifacts

**Files:**
- Modify: `src/main_graph/constants.py`, `src/main_graph/state.py`, `src/main_graph/graph.py`, `src/services/job_runner.py`
- Test: extend `tests/unit/test_graph_routing.py`

**Interfaces:**
- Consumes: `remediation_subgraph`; `REMEDIATION` constant; `MainState.remediation_result_id`.
- Produces: pipeline `PREP → ANALYSIS → REMEDIATION → REPORT → END`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_graph_routing.py` (mirror existing routing tests):
```python
def test_pipeline_includes_remediation_between_analysis_and_report():
    from src.main_graph.graph import build_main_graph
    from src.main_graph.constants import ANALYSIS, REMEDIATION, REPORT
    graph = build_main_graph()
    nodes = graph.get_graph().nodes
    assert REMEDIATION in nodes
    # analysis routes to remediation; remediation routes to report
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert (ANALYSIS, REMEDIATION) in edges or any(
        e.source == ANALYSIS and e.target == REMEDIATION for e in graph.get_graph().edges
    )
    assert any(e.source == REMEDIATION and e.target == REPORT
               for e in graph.get_graph().edges)
```
(If `test_graph_routing.py` asserts specific existing edges like `ANALYSIS -> REPORT`, update those assertions to the new `ANALYSIS -> REMEDIATION -> REPORT` shape.)

- [ ] **Step 2: Run, verify fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_graph_routing.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the edits**

`src/main_graph/constants.py` — add:
```python
REMEDIATION = "remediation"
```

`src/main_graph/state.py` — add to `MainState`:
```python
    remediation_result_id: NotRequired[str]
```

`src/main_graph/graph.py`:
```python
from src.main_graph.constants import ANALYSIS, PREP, REMEDIATION, REPORT
from src.main_graph.subgraphs.remediation import remediation_subgraph
...
def _after_analysis(state: MainState) -> str:
    if not state.get("analysis_result_id"):
        return END
    return REMEDIATION


def build_main_graph():
    builder = StateGraph(MainState)
    builder.add_node(PREP, discovery_subgraph)
    builder.add_node(ANALYSIS, analysis_subgraph)
    builder.add_node(REMEDIATION, remediation_subgraph)
    builder.add_node(REPORT, report_subgraph)

    builder.add_edge(START, PREP)
    builder.add_conditional_edges(PREP, _after_prep, [ANALYSIS, END])
    builder.add_conditional_edges(ANALYSIS, _after_analysis, [REMEDIATION, END])
    builder.add_edge(REMEDIATION, REPORT)
    builder.add_edge(REPORT, END)
    return builder.compile(checkpointer=InMemorySaver())
```

`src/services/job_runner.py` — in `_stream_graph`, extend the artifact wiring so REMEDIATION starts/completes and REPORT starts after it. Replace the ANALYSIS/REPORT branch block with:
```python
            if node_name in (PREP, ANALYSIS, REMEDIATION, REPORT):
                cost_now = cost_cb.cost()
                await dao.update_artifact_data(
                    job_id, node_name, {"cost": round(cost_now - prev_cost, 6)}
                )
                prev_cost = cost_now

            if node_name == PREP:
                status = "failed" if node_update.get("discovery_error") else "done"
                await dao.complete_artifact(job_id, PREP, status)
                if status == "done":
                    await dao.start_artifact(job_id, ANALYSIS)

            elif node_name == ANALYSIS:
                await dao.complete_artifact(job_id, ANALYSIS, "done")
                if node_update.get("analysis_result_id"):
                    await dao.start_artifact(job_id, REMEDIATION)

            elif node_name == REMEDIATION:
                await dao.complete_artifact(job_id, REMEDIATION, "done")
                rem_id = node_update.get("remediation_result_id")
                if rem_id:
                    result_dao = get_result_dao()
                    rem = await result_dao.get_remediation(rem_id)
                    await dao.update_artifact_data(
                        job_id, REMEDIATION, {"output": rem.model_dump()}
                    )
                await dao.start_artifact(job_id, REPORT)

            elif node_name == REPORT:
                report_result_id = node_update.get("report_result_id")
                await dao.complete_artifact(job_id, REPORT, "done")
                if report_result_id:
                    result_dao = get_result_dao()
                    report = await result_dao.get_report(report_result_id)
                    await dao.update_artifact_data(
                        job_id, REPORT, {"output": report.model_dump()}
                    )
```

In `_finalize`, extend the success branch to persist the remediation id too:
```python
    else:
        await dao.save_result(job_id, {
            "report_result_id": values.get("report_result_id", ""),
            "remediation_result_id": values.get("remediation_result_id", ""),
        })
```

Also add `REMEDIATION` to the constants import in `job_runner.py`:
```python
from src.main_graph.constants import ANALYSIS, PREP, REMEDIATION, REPORT
```

- [ ] **Step 4: Run, verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_graph_routing.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/constants.py src/main_graph/state.py src/main_graph/graph.py \
        src/services/job_runner.py tests/unit/test_graph_routing.py
git commit -m "feat(remediation): wire remediation phase into main graph + artifacts"
```

---

## Final verification (run after all tasks)

- [ ] Full suite: `cd apps/backend && uv run pytest -q` — all green (existing + new).
- [ ] Lint: `cd apps/backend && uv run ruff check .` — clean.
- [ ] Types: `cd apps/backend && uv run mypy src` — clean (match the project's existing mypy scope/command if different).
- [ ] Sanity: `cd apps/backend && uv run python -c "from src.main_graph.graph import build_main_graph; build_main_graph()"` — compiles with the remediation node.

## Deferred (explicit, tracked for later specs)

- **Live end-to-end validation** (unit tests mock the container + `gh`): manual run with `remediate=true` against `misi-e2e-validation-cve-direct` (`lodash@4.17.11`, non-major fix) once merged — the known gap from the spec. Treat unverified end-to-end until then.
- **Per-dep patch split** — v1 attaches the full working-copy diff to each fixed remediation. Splitting the diff per dep is a later refinement.
- **Report integration** — report currently ignores remediation output; the "report becomes a summarizer of outcomes" refactor is a separate spec.
- **Tier 2 (`bump_with_codemod`) and Tier 3 (`replace`)** — the skipped breadcrumbs feed these.
- **Frontend surfacing** of the remediation artifact/PR link.
