# Remediation Subgraph — Deepagent Tier Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the remediation subgraph's single-shot structured-output
orchestrator with a root deepagent that dispatches per-target subagents
(bump, adapt-code, or replace-package), discovers cross-target coupling,
and ships one PR per connected group — behind a deterministic verification
backstop that never trusts an agent's own self-report.

**Architecture:** `select_remediation_targets` (unchanged) seeds an initial
target set. A root `deepagents.create_deep_agent` dispatches one `task()`
call per open target to a single reusable `CompiledSubAgent`
(`remediate_target`). Each invocation runs its own *nested* deep agent on
its own isolated clone (`FilesystemBackend(root_dir=...)`), decides bump vs.
adapt vs. replace using release-notes/blast-radius/dependents evidence,
edits files itself, self-corrects against its own `verify` tool calls, and
finalizes via `response_format=RemediationOutcome`. A discovered `requires`
signal (any dependency, finding or not) causes the root to dispatch a new
target. Once the root is done, a deterministic `group_and_verify_gate`
computes connected groups from the discovered coupling, replays each
group's changes onto a clean clone, and re-verifies from scratch — this,
not any agent's claim, decides `status`. `pr_and_persist_node` opens one
PR per fixed group (consent-gated) and persists the result.

**Tech Stack:** Python, LangGraph, `deepagents>=0.6.12,<0.7`, Pydantic,
pytest / pytest-asyncio, `uv`.

**Reference spec:** `docs/superpowers/specs/2026-07-26-remediation-deepagent-tier-ladder.md`
(read it before starting — this plan implements its decisions D1–D10).

## Global Constraints

- Root deep agent `recursion_limit = 50` (`_RECURSION_LIMIT` in `deepagent/nodes.py`).
- Correction-round cap on `group_and_verify_gate` = 2 (`_MAX_CORRECTION_ROUNDS`).
- Total connected groups processed per job capped at 20 (`_MAX_GROUPS`);
  overflow groups ship as `skipped`, `skip_reason="target/group cap exceeded"`.
- No raw shell/`execute` tool is ever added to any agent's `tools=[...]`
  list, root or subagent (spec D3).
- `remediate=false` (consent flag, from `get_services(config)["remediate"]`)
  must result in zero `gh`/`git` calls across every group, every time —
  `Remediation` records and patches are still produced and persisted.
- A `Remediation.status` is only ever set to `"fixed"` by
  `group_and_verify_gate`'s independent re-verification — never copied
  from a subagent's own self-reported outcome.
- Per-target work always happens on an isolated clone
  (`workspace.copy_repo`), never a working copy shared between two targets
  running in the same root turn (spec D4).
- `Model.GPT_5_4_MINI` (from `src.utils.llm`) is the model for every LLM
  call added in this plan (root agent, nested per-target agent, structured
  extraction) — matches the model already used by the code this plan
  replaces (`orchestrator.py`'s `_llm = get_llm(Model.GPT_5_4_MINI)`).

---

## Task 1: Models — `RemediationOutcome`, `required_by`, per-group PR fields

**Files:**
- Modify: `apps/backend/src/models/remediation.py`
- Test: `apps/backend/tests/unit/models/test_remediation_models.py`

**Interfaces:**
- Produces: `Remediation.required_by: list[str]`, `Remediation.branch: str | None`,
  `Remediation.pr_url: str | None`, `RemediationOutcome` (new model). Consumed
  by every later task.
- Removes: `RemediationResult.branch`/`.pr_url` (moved onto `Remediation`,
  since PRs are now per-group, not per-job — spec D9), `RemediationDecision`
  (replaced by the agent's own tool loop — spec D2/D7). Grep the codebase
  for both before finishing this task; nothing outside `orchestrator.py`
  and its test (deleted in Task 9) should reference them, so no other
  breakage is expected, but confirm it.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/models/test_remediation_models.py`, and
change the existing round-trip test to match the new field locations:

```python
def test_remediation_carries_required_by_and_pr_fields():
    r = Remediation(addresses=[], target_dep="eslint-plugin-react", required_by=["eslint"])
    assert r.required_by == ["eslint"]
    assert r.branch is None
    assert r.pr_url is None


def test_remediation_outcome_defaults_are_bump_only():
    outcome = RemediationOutcome()
    assert outcome.strategy == "bump"
    assert outcome.requires == []
    assert outcome.code_diff == ""
    assert outcome.status == "skipped"


def test_remediation_outcome_replace_fields():
    outcome = RemediationOutcome(
        strategy="replace", replacement_dep="fast-glob",
        replacement_range="^3.0.0", requires=["some-plugin"],
    )
    assert outcome.replacement_dep == "fast-glob"
    assert outcome.requires == ["some-plugin"]
```

Replace `test_remediation_result_round_trip` (it currently asserts
`branch`/`pr_url` on `RemediationResult`) with:

```python
def test_remediation_result_round_trip():
    res = RemediationResult(
        job_id="j1",
        remediations=[
            Remediation(
                addresses=["minimist"],
                target_dep="mkdirp",
                strategy="bump",
                from_range="^0.5.1",
                to_range="^0.5.5",
                status="fixed",
                branch="remediation/j1-mkdirp",
                pr_url="https://gh/pr/1",
            )
        ],
        consent=True,
    )
    doc = res.model_dump()
    restored = RemediationResult(**doc)
    assert restored.remediations[0].target_dep == "mkdirp"
    assert restored.remediations[0].pr_url == "https://gh/pr/1"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -v`
Expected: FAIL (`RemediationOutcome` doesn't exist yet; `Remediation` has
no `required_by`/`branch`/`pr_url`; `RemediationResult(branch=..., pr_url=...)`
call in the old test signature errors once you've edited it).

- [ ] **Step 3: Edit the model**

Edit `apps/backend/src/models/remediation.py`. Add `required_by`, `branch`,
`pr_url` to `Remediation`; remove `branch`/`pr_url` from `RemediationResult`;
delete `RemediationDecision`; add `RemediationOutcome`:

```python
class Remediation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    addresses: list[str]
    target_dep: str
    required_by: list[str] = Field(default_factory=list)
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
    branch: str | None = None
    pr_url: str | None = None


class RemediationResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    remediations: list[Remediation] = Field(default_factory=list)
    consent: bool = False


class RemediationTarget(BaseModel):
    """Internal: a deduped unit of work produced by target selection."""
    target_dep: str
    addresses: list[str]
    current_range: str | None = None


class RemediationOutcome(BaseModel):
    """Structured final answer of one per-target remediation subagent
    (deepagents `response_format`). `status` here is the agent's OWN
    self-report and is provisional — group_and_verify_gate re-verifies
    independently and is the only thing that sets the Remediation record
    that actually ships. `code_diff` is a unified diff of any file edits
    the agent made (Tier 2/3 only; empty for a plain bump)."""
    strategy: Literal["bump", "bump_with_codemod", "replace"] = "bump"
    to_range: str | None = None
    replacement_dep: str | None = None
    replacement_range: str | None = None
    migration_plan: str = ""
    code_diff: str = ""
    requires: list[str] = Field(default_factory=list)
    status: Literal["fixed", "failed", "skipped"] = "skipped"
    skip_reason: str | None = None
    summary: str = ""
```

Delete the `RemediationDecision` class entirely (it directly precedes
`RemediationTarget`/follows it in the current file — remove it, keep
everything else in the file, including `VerificationResult`/`CodeChange`,
unchanged).

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -v`
Expected: PASS

- [ ] **Step 5: Grep for stale references**

Run: `cd apps/backend && grep -rn "RemediationDecision" src tests`
Expected: matches only in `src/main_graph/subgraphs/remediation/orchestrator.py`
and `tests/unit/subgraphs/remediation/test_orchestrator.py` — both deleted
in Task 9. If anything else references it, note it in your report; do not
fix it in this task.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/models/remediation.py apps/backend/tests/unit/models/test_remediation_models.py
git commit -m "feat(remediation): add RemediationOutcome, per-group PR fields, required_by"
```

---

## Task 2: `dependents_of` — non-finding reverse dependency lookup

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py`
- Test: `apps/backend/tests/unit/test_dependency_graph_helpers.py`

**Interfaces:**
- Produces: `dependents_of(graph: dict, name: str) -> list[str]`. Consumed
  by Task 4 (`make_dependents_of_tool`) and Task 6 (subagent's fallback
  `current_range` lookup uses `graph["direct"]`, not this function, so no
  circular dependency).
- Consumes: the existing flat `{"direct": {...}, "packages": {...}}` graph
  shape and the existing private `_package_name(flat_key)` helper already
  in this file — reuse it, don't duplicate it.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/test_dependency_graph_helpers.py` (uses the
`_GRAPH` fixture already defined at the top of that file):

```python
from src.main_graph.subgraphs.discovery.dependency_graph import dependents_of


def test_dependents_of_returns_immediate_parents_only():
    # qs is depended on directly by body-parser and webpack (not express,
    # which only reaches qs transitively through body-parser)
    assert dependents_of(_GRAPH, "qs") == ["body-parser", "webpack"]


def test_dependents_of_single_parent():
    assert dependents_of(_GRAPH, "body-parser") == ["express"]


def test_dependents_of_empty_for_a_leaf_nothing_depends_on():
    assert dependents_of(_GRAPH, "express") == []


def test_dependents_of_empty_when_no_transitive_data():
    graph = {"direct": {"lodash": "^4.17.21"}, "packages": {}}
    assert dependents_of(graph, "lodash") == []


def test_dependents_of_empty_graph():
    assert dependents_of({}, "anything") == []


def test_dependents_of_scoped_package_name():
    graph = {
        "direct": {"@nestjs/core": "10.0.0"},
        "packages": {
            "@nestjs/core@10.0.0": {"dependencies": ["@scope/leaf@1.0.0"]},
            "@scope/leaf@1.0.0": {"dependencies": []},
        },
    }
    assert dependents_of(graph, "@scope/leaf") == ["@nestjs/core"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/test_dependency_graph_helpers.py -v`
Expected: FAIL (`dependents_of` doesn't exist)

- [ ] **Step 3: Implement**

Add to `apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py`,
directly after `direct_dependents`:

```python
def dependents_of(graph: dict, name: str) -> list[str]:
    """Return every package name in the tree with a recorded dependency on
    any installed version of `name` - not limited to direct-dependency
    roots, unlike direct_dependents(). This is what lets a remediation
    agent check impact on packages that have no associated finding at all
    (e.g. "does anything else in this tree depend on eslint before I bump
    it"). Structural only: reflects the resolved graph, not whether a
    declared version range still holds after a bump - that is what
    verification checks.
    """
    packages = graph.get("packages") or {}
    if not packages:
        return []
    targets = {key for key in packages if _package_name(key) == name}
    if not targets:
        return []
    result = {
        _package_name(key)
        for key, info in packages.items()
        if any(child in targets for child in info.get("dependencies", []))
    }
    return sorted(result)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_dependency_graph_helpers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/discovery/dependency_graph.py apps/backend/tests/unit/test_dependency_graph_helpers.py
git commit -m "feat(remediation): add dependents_of reverse-dependency lookup"
```

---

## Task 3: `connected_groups` — pure grouping from discovered coupling

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/__init__.py` (empty)
- Create: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/grouping.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_grouping.py`

**Interfaces:**
- Produces: `connected_groups(target_deps: list[str], requires_edges: dict[str, list[str]]) -> list[list[str]]`.
  `requires_edges` maps a target's own dep name to the list of OTHER dep
  names it requires (this is the shape `RemediationDeepAgentState.requires_edges`
  uses — see Task 5). Consumed by Task 8 (`group_and_verify_gate`,
  `pr_and_persist_node`).

- [ ] **Step 1: Write the failing tests**

```python
from src.main_graph.subgraphs.remediation.deepagent.grouping import connected_groups


def test_no_edges_every_target_is_its_own_group():
    assert connected_groups(["a", "b"], {}) == [["a"], ["b"]]


def test_chain_of_requires_forms_one_group():
    edges = {"a": ["b"], "b": ["c"]}
    assert connected_groups(["a"], edges) == [["a", "b", "c"]]


def test_independent_pairs_stay_separate():
    edges = {"a": ["b"], "c": ["d"]}
    assert connected_groups(["a", "c"], edges) == [["a", "b"], ["c", "d"]]


def test_companion_only_dependency_is_included():
    # "b" never appears in target_deps, only as something "a" requires
    edges = {"a": ["b"]}
    assert connected_groups(["a"], edges) == [["a", "b"]]


def test_empty_input():
    assert connected_groups([], {}) == []


def test_groups_and_members_are_sorted_for_determinism():
    edges = {"z": ["y"], "x": []}
    assert connected_groups(["z", "x"], edges) == [["x"], ["y", "z"]]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_grouping.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Implement**

Create `apps/backend/src/main_graph/subgraphs/remediation/deepagent/__init__.py`
(empty file).

Create `apps/backend/src/main_graph/subgraphs/remediation/deepagent/grouping.py`:

```python
from __future__ import annotations


def connected_groups(
    target_deps: list[str], requires_edges: dict[str, list[str]]
) -> list[list[str]]:
    """Compute connected groups of dependency names from discovered
    `requires` edges (target dep name -> list of other dep names it
    requires). Every name in `target_deps` appears in exactly one group -
    an unconnected target is a group of one (the common case). A name that
    only ever appears as a `requires` value, never independently in
    `target_deps`, is still included in whichever group pulled it in - it
    exists only because something else needs it (spec D8). Groups are
    sorted by their smallest member name, and each group's members are
    sorted, so output is deterministic and testable.
    """
    parent: dict[str, str] = {}

    def find(name: str) -> str:
        parent.setdefault(name, name)
        root = name
        while parent[root] != root:
            root = parent[root]
        while parent[name] != root:
            parent[name], name = root, parent[name]
        return root

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    all_names: set[str] = set(target_deps)
    for target, requires in requires_edges.items():
        all_names.add(target)
        all_names.update(requires)
    for name in all_names:
        find(name)
    for target, requires in requires_edges.items():
        for required in requires:
            union(target, required)

    groups: dict[str, list[str]] = {}
    for name in sorted(all_names):
        groups.setdefault(find(name), []).append(name)
    return sorted(groups.values(), key=lambda group: group[0])
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_grouping.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/__init__.py apps/backend/src/main_graph/subgraphs/remediation/deepagent/grouping.py apps/backend/tests/unit/subgraphs/remediation/test_grouping.py
git commit -m "feat(remediation): add connected_groups for discovered dependency coupling"
```

---

## Task 4: Per-target agent tools

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/tools.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_tools.py`

**Interfaces:**
- Produces: `make_read_release_notes_tool(repo_path, container, docker_image)`,
  `make_dependents_of_tool(dependency_graph)`, `make_bump_dependency_tool(work_dir)`,
  `make_verify_tool(work_dir, container, docker_image, package_manager, default_targeted_deps)`
  — each returns a `@tool`-decorated LangChain tool. Consumed by Task 6
  (`subagent_wrapper.py`), alongside the already-existing
  `make_blast_radius_tool` (`src/main_graph/tools/blast_radius.py`) and
  `make_search_code_tool` (`src/main_graph/tools/search_code.py`), which
  this task does not touch — it only adds the tools those two don't cover.
- Consumes: `apply_bump`/`copy_repo` from `workspace.py` (unchanged),
  `verify_working_copy` from `verify.py` (unchanged), `dependents_of`
  from Task 2.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_tools.py`:

```python
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_dependents_of_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)


class FakeContainer:
    """Returns queued (rc, stdout, stderr) per run() call, in order."""
    def __init__(self, results):
        self._results = list(results)
        self.commands = []

    async def run(self, image, command, volume=None, run_as_root=False, secret_env=None):
        self.commands.append(command)
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_read_release_notes_returns_unavailable_when_repo_unresolved():
    container = FakeContainer([(1, "", "npm error 404 Not Found")])
    tool = make_read_release_notes_tool("/repo", container, "node:lts-alpine")
    result = await tool.ainvoke({"package_name": "left-pad"})
    assert result["available"] is False


@pytest.mark.asyncio
async def test_read_release_notes_success():
    container = FakeContainer([(0, "git+https://github.com/eslint/eslint.git\n", "")])
    releases_json = json.dumps(
        [{"tag_name": "v9.0.0", "name": "9.0.0", "body": "breaking: flat config"}]
    ).encode()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(releases_json, b""))
    fake_proc.returncode = 0

    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.tools.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    ):
        tool = make_read_release_notes_tool("/repo", container, "node:lts-alpine")
        result = await tool.ainvoke({"package_name": "eslint"})

    assert result["available"] is True
    assert result["repository"] == "eslint/eslint"
    assert result["releases"][0]["tag"] == "v9.0.0"


@pytest.mark.asyncio
async def test_dependents_of_tool_delegates_to_pure_function():
    graph = {
        "direct": {"a": "1.0.0"},
        "packages": {
            "a@1.0.0": {"dependencies": ["b@1.0.0"]},
            "b@1.0.0": {"dependencies": []},
        },
    }
    tool = make_dependents_of_tool(graph)
    result = await tool.ainvoke({"package_name": "b"})
    assert result == ["a"]


@pytest.mark.asyncio
async def test_bump_dependency_tool_reports_not_applied(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {}}')
    tool = make_bump_dependency_tool(str(tmp_path))
    result = await tool.ainvoke({"target_dep": "left-pad", "to_range": "^2.0.0"})
    assert result == {"applied": False}


@pytest.mark.asyncio
async def test_bump_dependency_tool_applies(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "^1.0.0"}})
    )
    tool = make_bump_dependency_tool(str(tmp_path))
    result = await tool.ainvoke({"target_dep": "left-pad", "to_range": "^2.0.0"})
    assert result == {"applied": True}
    updated = json.loads((tmp_path / "package.json").read_text())
    assert updated["dependencies"]["left-pad"] == "^2.0.0"


@pytest.mark.asyncio
async def test_verify_tool_reports_installed(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}))
    container = FakeContainer([(0, "", ""), (0, "{}", "")])
    tool = make_verify_tool(str(tmp_path), container, "node:lts-alpine", "npm", ["eslint"])
    result = await tool.ainvoke({})
    assert result["installed"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_tools.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Implement**

Create `apps/backend/src/main_graph/subgraphs/remediation/deepagent/tools.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
import re

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.discovery.dependency_graph import dependents_of
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import apply_bump

logger = logging.getLogger(__name__)

_GITHUB_REPO_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?\s*$")


async def _resolve_github_repo(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> tuple[str, str] | None:
    command = f"cd /workspace && npm view {package_name} repository.url"
    rc, stdout, _stderr = await container.run(
        image=docker_image,
        command=command,
        volume=f"{repo_path}:/workspace",
        run_as_root=True,
    )
    if rc != 0:
        return None
    match = _GITHUB_REPO_RE.search(stdout.strip())
    return (match.group(1), match.group(2)) if match else None


def make_read_release_notes_tool(
    repo_path: str, container: ContainerRunPort, docker_image: str
):
    @tool
    async def read_release_notes(package_name: str) -> dict:
        """Fetch recent GitHub release notes for an npm package, resolved
        via its registry-declared repository URL. Use this to check for
        breaking changes between the installed version and a candidate
        upgrade before deciding whether a bump is safe, or whether code
        needs to change too."""
        resolved = await _resolve_github_repo(
            package_name, repo_path, container, docker_image
        )
        if resolved is None:
            return {
                "package_name": package_name,
                "available": False,
                "error": "could not resolve a GitHub repository for this package",
            }
        owner, repo = resolved
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "api", f"repos/{owner}/{repo}/releases", "--paginate",
                "-q", ".[:20]",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
        except FileNotFoundError:
            return {
                "package_name": package_name,
                "available": False,
                "error": "gh CLI not found",
            }
        if proc.returncode != 0:
            return {
                "package_name": package_name,
                "available": False,
                "error": err.decode(errors="replace")[:300],
            }
        try:
            releases = json.loads(out.decode(errors="replace") or "[]")
        except json.JSONDecodeError:
            return {
                "package_name": package_name,
                "available": False,
                "error": "unparseable gh output",
            }
        return {
            "package_name": package_name,
            "available": True,
            "repository": f"{owner}/{repo}",
            "releases": [
                {
                    "tag": release.get("tag_name"),
                    "name": release.get("name"),
                    "body": (release.get("body") or "")[:2000],
                }
                for release in releases
            ],
        }

    return read_release_notes


def make_dependents_of_tool(dependency_graph: dict):
    @tool
    def dependents_of_tool(package_name: str) -> list[str]:
        """Return every package in this project's dependency tree that
        depends on `package_name`, whether or not it has a flagged finding.
        Structural only - does not confirm a declared version range still
        holds after a bump; call `verify` for that."""
        return dependents_of(dependency_graph, package_name)

    return dependents_of_tool


def make_bump_dependency_tool(work_dir: str):
    @tool
    def bump_dependency(target_dep: str, to_range: str) -> dict:
        """Edit package.json to set target_dep's declared range to
        to_range. Returns {"applied": false} if target_dep isn't declared
        in dependencies/devDependencies."""
        return {"applied": apply_bump(work_dir, target_dep, to_range)}

    return bump_dependency


def make_verify_tool(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
    default_targeted_deps: list[str],
):
    @tool
    async def verify(targeted_deps: list[str] | None = None) -> dict:
        """Install, build (if scripted), test (if scripted), and re-audit
        the working copy. Use this to self-correct as you iterate - it is
        a guide for your own next step, not the final verdict: a separate
        deterministic check re-verifies from a clean clone before anything
        ships."""
        result = await verify_working_copy(
            work_dir,
            container,
            docker_image,
            package_manager,
            targeted_deps or default_targeted_deps,
        )
        return result.model_dump()

    return verify
```

Note the test patches
`src.main_graph.subgraphs.remediation.deepagent.tools.asyncio.create_subprocess_exec`
(module-qualified) rather than the global `asyncio.create_subprocess_exec` —
this is required because `tools.py` does `import asyncio` and calls
`asyncio.create_subprocess_exec`, so the patch target must go through the
same module reference.

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/tools.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_tools.py
git commit -m "feat(remediation): add per-target agent tools (release notes, dependents, bump, verify)"
```

---

## Task 5: `RemediationDeepAgentState` + extend the outer `RemediationState`

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/state.py`
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/state.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_state.py`

**Interfaces:**
- Produces: `RemediationDeepAgentState(DeepAgentState)` with fields
  `job_id`, `prep_result_id`, `evidence`, `targets`, `remediations`,
  `requires_edges`; and the reusable reducer `_merge_replace` (dict-keyed,
  incoming-wins-per-key merge). Consumed by Task 6 (subagent's own state
  schema uses the same `_merge_replace` for its matching fields) and
  Task 8 (`root_deepagent_node` builds the root agent with
  `state_schema=RemediationDeepAgentState`).
- Also extends the outer `RemediationState` (the subgraph-level TypedDict
  every node function in Task 8 reads/writes) with the same
  `targets`/`evidence`/`remediations`/`requires_edges` fields plus
  `retry_targets`/`correction_rounds`, importing `_merge_replace` from this
  task's new module rather than redefining it. **This has to happen now,
  not in Task 9 where the rest of the graph/state wiring lands** — Task 8's
  `nodes.py` reads `state["targets"]`/`state["remediations"]` etc. on
  `RemediationState`, and if that TypedDict isn't extended until after
  Task 8, mypy fails on Task 8's own diff. Task 9 only touches `graph.py`
  and deletes the old orchestrator.

**Why `_merge_replace`, not `operator.add`:** `remediations`/`requires_edges`
are keyed by target dep name. A correction round only re-dispatches the
targets that failed verification (Task 8), producing a *fresh* outcome for
that same dep name - with `operator.add` (list accumulation) the stale
first-round entry and the new second-round entry would both remain,
duplicating that target's `Remediation` record. Keying by dep name and
letting the incoming write replace the existing one avoids reintroducing
the exact double-counting class of bug the analysis-subgraph swap had to
fix after the fact (see spec D5) - by construction, not by later cleanup.
It is also safe under two parallel `task()` calls in the same root turn,
since they write disjoint keys (different target dep names) and dict-merge
requires no ordering between them, unlike list-concat.

- [ ] **Step 1: Write the failing tests**

```python
from src.main_graph.subgraphs.remediation.deepagent.state import (
    RemediationDeepAgentState,
    _keep_first_dict,
    _keep_first_str,
    _merge_replace,
)


def test_keep_first_str_keeps_existing_truthy_value():
    assert _keep_first_str("job-1", "job-2") == "job-1"


def test_keep_first_str_takes_incoming_when_current_empty():
    assert _keep_first_str("", "job-2") == "job-2"


def test_keep_first_dict_keeps_existing_truthy_value():
    assert _keep_first_dict({"a": 1}, {"b": 2}) == {"a": 1}


def test_keep_first_dict_takes_incoming_when_current_empty():
    assert _keep_first_dict({}, {"b": 2}) == {"b": 2}


def test_merge_replace_incoming_key_wins():
    current = {"eslint": {"status": "skipped"}}
    incoming = {"eslint": {"status": "fixed"}}
    assert _merge_replace(current, incoming) == {"eslint": {"status": "fixed"}}


def test_merge_replace_keeps_disjoint_keys_from_both():
    current = {"a": {"x": 1}}
    incoming = {"b": {"y": 2}}
    assert _merge_replace(current, incoming) == {"a": {"x": 1}, "b": {"y": 2}}


def test_state_schema_declares_expected_fields():
    hints = RemediationDeepAgentState.__annotations__
    for field in ("job_id", "prep_result_id", "evidence", "targets", "remediations", "requires_edges"):
        assert field in hints
```

Also create `apps/backend/tests/unit/subgraphs/remediation/test_state.py`
with a check that the outer state accepts the new keys without error,
since `TypedDict` gives no runtime enforcement and the only real check
available is a type-checker one:

```python
def test_remediation_state_accepts_new_deepagent_fields():
    from src.main_graph.subgraphs.remediation.state import RemediationState

    state: RemediationState = {
        "job_id": "j1", "concern": "c", "prep_result_id": "p1", "analysis_result_id": "a1",
        "targets": {}, "evidence": {}, "remediations": {}, "requires_edges": {},
        "retry_targets": [], "correction_rounds": 0,
    }
    assert state["correction_rounds"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_state.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Implement**

Create `apps/backend/src/main_graph/subgraphs/remediation/deepagent/state.py`:

```python
from __future__ import annotations

from typing import Annotated

from deepagents import DeepAgentState


def _keep_first_str(current: str, incoming: str) -> str:
    return current or incoming


def _keep_first_dict(current: dict, incoming: dict) -> dict:
    return current or incoming


def _merge_replace(current: dict, incoming: dict) -> dict:
    """Dict-keyed merge where the incoming write wins per key. Used for
    per-target accumulation so a retry round's fresh outcome for a target
    replaces its earlier attempt instead of appending a duplicate, and so
    two parallel task() calls writing different target keys in the same
    superstep merge cleanly with no ordering requirement between them."""
    return {**current, **incoming}


class RemediationDeepAgentState(DeepAgentState):
    job_id: Annotated[str, _keep_first_str]
    prep_result_id: Annotated[str, _keep_first_str]
    evidence: Annotated[dict, _keep_first_dict]
    targets: Annotated[dict[str, dict], _keep_first_dict]
    remediations: Annotated[dict[str, dict], _merge_replace]
    requires_edges: Annotated[dict[str, list], _merge_replace]
```

Replace `apps/backend/src/main_graph/subgraphs/remediation/state.py`
(the outer subgraph state — do not confuse with the file just created
above):

```python
from __future__ import annotations

from typing import Annotated, NotRequired

from typing_extensions import TypedDict

from src.main_graph.subgraphs.remediation.deepagent.state import _merge_replace


class RemediationState(TypedDict):
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str
    remediation_result_id: NotRequired[str]
    targets: NotRequired[dict[str, dict]]
    evidence: NotRequired[dict]
    remediations: NotRequired[Annotated[dict[str, dict], _merge_replace]]
    requires_edges: NotRequired[Annotated[dict[str, list], _merge_replace]]
    retry_targets: NotRequired[list[str]]
    correction_rounds: NotRequired[int]
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/ -v -k "state"`
Expected: PASS (both the new `test_deepagent_state.py` and the
`test_remediation_state_accepts_new_deepagent_fields` test added above)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/state.py apps/backend/src/main_graph/subgraphs/remediation/state.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_state.py apps/backend/tests/unit/subgraphs/remediation/test_state.py
git commit -m "feat(remediation): add RemediationDeepAgentState, extend outer RemediationState"
```

---

## Task 6: Per-target subagent (`remediate_target`)

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_subagent_wrapper.py`

**Interfaces:**
- Produces: `build_target_subagent() -> CompiledSubAgent` (a `TypedDict`
  literal `{"name": "remediate_target", "description": ..., "runnable": ...}`
  — takes no arguments, built once at import time as a module-level
  singleton, exactly mirroring
  `apps/backend/.worktrees/analysis-deepagent-swap/apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py::build_agent_subagent`
  in the sibling analysis-subgraph swap — **read that file first, it is a
  real, tested, working example of this exact mechanism in this codebase**;
  match its structure (plain-dict return from `_run`, `get_services(config)`
  fetched fresh inside `_run`, `state["messages"][-1].content` for the
  incoming task description, structured-output extraction via
  `with_structured_output`) rather than inventing a different shape.
- Consumes: Task 1's `RemediationOutcome`/`Remediation`/`RemediationTarget`,
  Task 4's tool factories plus the existing `make_blast_radius_tool`
  (`src/main_graph/tools/blast_radius.py`) and `make_search_code_tool`
  (`src/main_graph/tools/search_code.py`), Task 5's `_merge_replace`,
  `workspace.copy_repo`.

**A residual uncertainty to verify empirically, not assume:** whether
`create_deep_agent(..., response_format=RemediationOutcome)`'s returned
`result["structured_response"]` is already a `RemediationOutcome` instance
or a plain dict needing `RemediationOutcome.model_validate(...)`. The code
below handles both defensively - keep that defensive handling rather than
assuming one or the other, and note in your report which one you actually
observed once the tests exercise it for real (Task 10's integration test is
where this gets exercised against real `deepagents` machinery; this task's
own unit tests use a stub that bypasses the question entirely).

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_target_subagent,
)
from src.models.remediation import RemediationOutcome
from src.models.results import PrepResult


def _prep(**overrides):
    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        docker_image="node:lts-alpine",
        detected_package_manager="npm",
        dependency_graph={"direct": {"eslint": "8.0.0"}, "packages": {}},
        vector_store_id="",
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


class _FakeHumanMessage:
    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_run_resolves_known_target_and_reports_outcome(tmp_path):
    spec = build_target_subagent()
    assert spec["name"] == "remediate_target"

    prep = _prep(repo_path=str(tmp_path))
    (tmp_path / "package.json").write_text('{"dependencies": {"eslint": "8.0.0"}}')

    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    container = MagicMock()
    config = {"configurable": {"result_dao": dao, "container": container}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value=str(tmp_path),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(
            return_value={
                "structured_response": RemediationOutcome(
                    strategy="bump", to_range="^9.0.0", summary="clean bump"
                )
            }
        )
        mock_create.return_value = nested_agent

        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {"eslint": {"target_dep": "eslint", "addresses": ["eslint"], "current_range": "8.0.0"}},
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    assert result["remediations"]["eslint"]["to_range"] == "^9.0.0"
    assert result["remediations"]["eslint"]["status"] == "skipped"  # provisional, gate sets the real value
    assert result["requires_edges"] == {}


@pytest.mark.asyncio
async def test_run_records_requires_edge():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    spec = build_target_subagent()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value="/tmp/fake-clone",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(
            return_value={
                "structured_response": RemediationOutcome(
                    strategy="bump_with_codemod",
                    to_range="^9.0.0",
                    requires=["eslint-plugin-react"],
                    summary="bumped and adapted call sites; plugin needs a bump too",
                )
            }
        )
        mock_create.return_value = nested_agent

        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {"eslint": {"target_dep": "eslint", "addresses": ["eslint"], "current_range": "8.0.0"}},
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    assert result["requires_edges"]["eslint"] == ["eslint-plugin-react"]


@pytest.mark.asyncio
async def test_run_synthesizes_target_for_unknown_dep_name():
    prep = _prep(dependency_graph={"direct": {"eslint-plugin-react": "^7.0.0"}, "packages": {}})
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    spec = build_target_subagent()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint-plugin-react"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value="/tmp/fake-clone",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(
            return_value={"structured_response": RemediationOutcome(strategy="bump", to_range="^8.0.0")}
        )
        mock_create.return_value = nested_agent

        # note: "targets" does NOT contain eslint-plugin-react - it must be
        # synthesized from the dependency graph's direct-range lookup
        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint-plugin-react."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {},
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    remediation = result["remediations"]["eslint-plugin-react"]
    assert remediation["from_range"] == "^7.0.0"
    assert remediation["addresses"] == []


@pytest.mark.asyncio
async def test_run_reports_failed_when_agent_produces_no_structured_response():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    spec = build_target_subagent()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value="/tmp/fake-clone",
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(return_value={})
        mock_create.return_value = nested_agent

        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {"eslint": {"target_dep": "eslint", "addresses": [], "current_range": "8.0.0"}},
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    assert result["remediations"]["eslint"]["status"] == "failed"
    assert result["remediations"]["eslint"]["skip_reason"] == "agent produced no structured decision"
```

Check `src/models/results.py::PrepResult`'s exact field list before writing
`_prep()` above — match its real required fields (add any this plan's draft
omitted; the four used here — `dependency_graph`, `docker_image`,
`detected_package_manager`, `vector_store_id`, `repo_path` — are the ones
this task's code actually reads, but `PrepResult` may require others to
construct at all).

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_subagent_wrapper.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Implement**

Read `apps/backend/.worktrees/analysis-deepagent-swap/apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py`
first (it exists on disk in the sibling worktree, real and tested).

Create `apps/backend/src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py`:

```python
"""Builds the single reusable per-target CompiledSubAgent (spec D2).

The root deep agent communicates a target as free text (deepagents' task()
tool has no way to pass a typed value), so this node's first step is a
small structured-output call converting that text back into a target dep
name - same pattern as the analysis-subgraph swap's
_extract_dispatch/AgentDispatch, applied here for a bare string instead of
a richer type.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, cast

from deepagents import CompiledSubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.deepagent.state import _merge_replace
from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_dependents_of_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.search_code import make_search_code_tool
from src.models.remediation import Remediation, RemediationOutcome, RemediationTarget
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_PROMPT = """\
You are remediating ONE dependency risk in a Node.js project: {target_dep}
(currently {current_range}).

Findings this addresses: {addresses}

Evidence (npm audit fix paths, outdated versions):
{evidence}

Steps:
1. Call read_release_notes to review what changed between the installed
   version and reasonable upgrade candidates.
2. Call blast_radius and search_code to see how {target_dep} is actually
   used in this codebase.
3. Decide: if nothing relevant broke, bump only. If something broke but you
   can fix the call sites yourself, bump AND edit the affected files. If
   the dependency itself should be replaced (abandoned, superseded, or the
   evidence above already says so), propose a replacement and migrate all
   usage yourself.
4. If your investigation shows another dependency must also move for this
   fix to be coherent (e.g. a peer/plugin no longer compatible), call
   dependents_of to confirm it is really in this tree, then list it in
   `requires` on your final answer - do not try to fix it yourself.
5. Apply your change with bump_dependency and/or direct file edits, then
   call verify. Iterate until satisfied or you conclude there is no safe
   fix - your own verify result guides your next step, it is not the final
   word on whether this ships.
6. Finish with your structured answer, including a short `summary` and, if
   you made file edits, the unified diff of those edits in `code_diff`.
"""


class _TargetDepExtraction(BaseModel):
    target_dep: str


async def _extract_target_dep(description: str, known_targets: list[str]) -> str:
    structured = _llm.with_structured_output(_TargetDepExtraction, method="function_calling")
    result = cast(
        _TargetDepExtraction,
        await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract which npm package this remediation task "
                        "description is about. Known open targets: "
                        f"{', '.join(known_targets) or 'none yet'}."
                    ),
                },
                {"role": "user", "content": description},
            ]
        ),
    )
    return result.target_dep


class _TargetSubagentState(TypedDict):
    messages: list
    job_id: str
    prep_result_id: str
    evidence: dict
    targets: dict[str, dict]
    remediations: Annotated[dict[str, dict], _merge_replace]
    requires_edges: Annotated[dict[str, list], _merge_replace]


async def _run(state: _TargetSubagentState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    targets = state.get("targets") or {}
    task_description = state["messages"][-1].content
    target_dep = await _extract_target_dep(task_description, list(targets))

    target_dict = targets.get(target_dep)
    if target_dict is not None:
        target = RemediationTarget(**target_dict)
    else:
        current_range = (prep.dependency_graph.get("direct") or {}).get(target_dep)
        target = RemediationTarget(target_dep=target_dep, addresses=[], current_range=current_range)

    work_dir = copy_repo(prep.repo_path)
    default_targeted = [target.target_dep, *target.addresses]
    tools = [
        make_read_release_notes_tool(work_dir, container, prep.docker_image),
        make_blast_radius_tool(work_dir, container, prep.docker_image),
        make_dependents_of_tool(prep.dependency_graph),
        make_bump_dependency_tool(work_dir),
        make_verify_tool(
            work_dir, container, prep.docker_image, prep.detected_package_manager, default_targeted
        ),
    ]
    if prep.vector_store_id:
        tools.append(make_search_code_tool(prep.vector_store_id))

    nested = create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        tools=tools,
        system_prompt=_SYSTEM_PROMPT.format(
            target_dep=target.target_dep,
            current_range=target.current_range or "unknown",
            addresses=", ".join(target.addresses)
            or "none (this dependency was pulled in because remediating another target requires it)",
            evidence=json.dumps(state.get("evidence") or {})[:4000],
        ),
        backend=FilesystemBackend(root_dir=work_dir),
        response_format=RemediationOutcome,
    )
    result = await nested.ainvoke(
        {"messages": [{"role": "user", "content": f"Remediate {target.target_dep}."}]},
        config,
    )
    raw_outcome = result.get("structured_response")
    outcome: RemediationOutcome | None
    if isinstance(raw_outcome, RemediationOutcome):
        outcome = raw_outcome
    elif raw_outcome is not None:
        outcome = RemediationOutcome.model_validate(raw_outcome)
    else:
        outcome = None

    if outcome is None:
        remediation = Remediation(
            addresses=target.addresses,
            target_dep=target.target_dep,
            from_range=target.current_range,
            status="failed",
            skip_reason="agent produced no structured decision",
        )
        return {
            "messages": [],
            "remediations": {target.target_dep: remediation.model_dump()},
            "requires_edges": {},
        }

    remediation = Remediation(
        addresses=target.addresses,
        target_dep=target.target_dep,
        strategy=outcome.strategy,
        from_range=target.current_range,
        to_range=outcome.to_range,
        replacement_dep=outcome.replacement_dep,
        replacement_range=outcome.replacement_range,
        migration_plan=outcome.migration_plan,
        patch=outcome.code_diff,
        status="skipped",  # provisional - group_and_verify_gate sets the real value
        skip_reason=outcome.skip_reason,
    )
    requires_edges = {target.target_dep: outcome.requires} if outcome.requires else {}
    return {
        "messages": [],
        "remediations": {target.target_dep: remediation.model_dump()},
        "requires_edges": requires_edges,
    }


def build_target_subagent() -> CompiledSubAgent:
    graph = StateGraph(_TargetSubagentState)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return {
        "name": "remediate_target",
        "description": (
            "Investigate and remediate ONE dependency risk. Describe which "
            "dependency to work on by name. Reviews release notes and real "
            "usage, decides bump vs. bump-and-adapt-code vs. replace, edits "
            "files and verifies its own work, and reports a structured "
            "outcome including any OTHER dependency that must also move "
            "for this fix to be coherent."
        ),
        "runnable": graph.compile(),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_subagent_wrapper.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py apps/backend/tests/unit/subgraphs/remediation/test_subagent_wrapper.py
git commit -m "feat(remediation): add remediate_target per-target subagent"
```

---

## Task 7: Group replay + deterministic re-verification (the backstop)

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/workspace.py`
  (add `replace_dependency`)
- Create: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/replay.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_replay.py`
- Test: extend `apps/backend/tests/unit/subgraphs/remediation/test_workspace.py`

**Interfaces:**
- Produces: `apply_group_changes(work_dir, members: list[Remediation]) -> bool`,
  `replay_and_verify_group(members, base_repo_path, container, docker_image, package_manager) -> VerificationResult`
  (spec D6 — the deterministic backstop). Consumed by Task 8
  (`group_and_verify_gate` calls `replay_and_verify_group`;
  `pr_and_persist_node` calls `apply_group_changes` again on a fresh clone
  to prepare the PR branch, reusing the same replay logic rather than
  re-implementing it).
- Consumes: Task 1's `Remediation`, `workspace.apply_bump`/`copy_repo`/
  new `replace_dependency`, `verify.verify_working_copy` (all unchanged
  except the one addition below).

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_workspace.py`:

```python
def test_replace_dependency_swaps_the_key(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"old-pkg": "^1.0.0"}})
    )
    assert replace_dependency(str(tmp_path), "old-pkg", "new-pkg", "^1.0.0") is True
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert "old-pkg" not in pkg["dependencies"]
    assert pkg["dependencies"]["new-pkg"] == "^1.0.0"


def test_replace_dependency_false_when_not_declared(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}))
    assert replace_dependency(str(tmp_path), "old-pkg", "new-pkg", "^1.0.0") is False
```

(add `import json` at the top of the file if not already present, and add
`replace_dependency` to the existing `from src.main_graph...workspace
import (...)` line.)

Create `apps/backend/tests/unit/subgraphs/remediation/test_replay.py`:

```python
from __future__ import annotations

import json

import pytest

from src.main_graph.subgraphs.remediation.deepagent.replay import (
    apply_group_changes,
    replay_and_verify_group,
)
from src.models.remediation import Remediation


class FakeContainer:
    def __init__(self, results):
        self._results = list(results)

    async def run(self, image, command, volume=None, run_as_root=False, secret_env=None):
        return self._results.pop(0)


def _bump(target_dep="lodash", to_range="^4.17.21"):
    return Remediation(
        addresses=[target_dep], target_dep=target_dep,
        strategy="bump", from_range="^4.17.11", to_range=to_range,
    )


@pytest.mark.asyncio
async def test_apply_group_changes_bumps_declared_dependency(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.11"}})
    )
    ok = await apply_group_changes(str(tmp_path), [_bump()])
    assert ok is True
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["dependencies"]["lodash"] == "^4.17.21"


@pytest.mark.asyncio
async def test_apply_group_changes_false_when_bump_target_missing(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}))
    ok = await apply_group_changes(str(tmp_path), [_bump()])
    assert ok is False


@pytest.mark.asyncio
async def test_replay_and_verify_group_runs_full_verification(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.11"}, "scripts": {}})
    )
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: str(tmp_path),
    )
    audit = json.dumps({"vulnerabilities": {}})
    container = FakeContainer([(0, "", ""), (0, audit, "")])

    result = await replay_and_verify_group([_bump()], "/original/repo", container, "node:lts-alpine", "npm")

    assert result.installed is True
    assert result.finding_resolved is True


@pytest.mark.asyncio
async def test_replay_and_verify_group_reports_apply_failure(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}))
    monkeypatch.setattr(
        "src.main_graph.subgraphs.remediation.deepagent.replay.copy_repo",
        lambda src: str(tmp_path),
    )
    result = await replay_and_verify_group([_bump()], "/original/repo", FakeContainer([]), "node:lts-alpine", "npm")
    assert result.installed is False
    assert "failed to apply" in result.logs_snippet
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_workspace.py tests/unit/subgraphs/remediation/test_replay.py -v`
Expected: FAIL (`replace_dependency`/`deepagent.replay` don't exist)

- [ ] **Step 3: Implement**

Add to `apps/backend/src/main_graph/subgraphs/remediation/workspace.py`,
directly after `apply_bump`:

```python
def replace_dependency(work_dir: str, old_dep: str, new_dep: str, new_range: str) -> bool:
    pkg_path = os.path.join(work_dir, "package.json")
    with open(pkg_path) as f:
        pkg = json.load(f)
    for section in ("dependencies", "devDependencies"):
        bucket = pkg.get(section) or {}
        if old_dep in bucket:
            del bucket[old_dep]
            bucket[new_dep] = new_range
            with open(pkg_path, "w") as f:
                json.dump(pkg, f, indent=2)
                f.write("\n")
            return True
    return False
```

Create `apps/backend/src/main_graph/subgraphs/remediation/deepagent/replay.py`:

```python
from __future__ import annotations

import asyncio
import logging
import shutil

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import (
    apply_bump,
    copy_repo,
    replace_dependency,
)
from src.models.remediation import Remediation, VerificationResult

logger = logging.getLogger(__name__)


async def _git_apply(work_dir: str, patch: str) -> bool:
    if not patch.strip():
        return True
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", work_dir, "apply", "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate(input=patch.encode())
    if proc.returncode != 0:
        logger.warning("git apply failed: %s", err.decode(errors="replace")[:500])
        return False
    return True


async def apply_group_changes(work_dir: str, members: list[Remediation]) -> bool:
    """Deterministically replay a settled group's changes onto a working
    copy: structured bumps/replacements applied declaratively (never a raw
    patch for package.json, to avoid manifest merge conflicts), code
    changes (Tier 2/3) via `git apply` of each member's own diff. Returns
    False if any member's change fails to apply - the caller must not
    treat a partial apply as success."""
    ok = True
    for member in members:
        if member.strategy == "replace" and member.replacement_dep and member.replacement_range:
            if not replace_dependency(
                work_dir, member.target_dep, member.replacement_dep, member.replacement_range
            ):
                ok = False
        elif member.to_range:
            if not apply_bump(work_dir, member.target_dep, member.to_range):
                ok = False
        if member.patch and not await _git_apply(work_dir, member.patch):
            ok = False
    return ok


async def replay_and_verify_group(
    members: list[Remediation],
    base_repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
) -> VerificationResult:
    """The deterministic backstop (spec D6): replay a settled group's
    changes onto a fresh clean clone and re-run full verification from
    scratch. Never trusts any member's own self-reported status."""
    work_dir = copy_repo(base_repo_path)
    try:
        if not await apply_group_changes(work_dir, members):
            return VerificationResult(logs_snippet="one or more changes failed to apply cleanly")
        targeted = sorted({dep for m in members for dep in [m.target_dep, *m.addresses]})
        return await verify_working_copy(work_dir, container, docker_image, package_manager, targeted)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_workspace.py tests/unit/subgraphs/remediation/test_replay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/workspace.py apps/backend/src/main_graph/subgraphs/remediation/deepagent/replay.py apps/backend/tests/unit/subgraphs/remediation/test_workspace.py apps/backend/tests/unit/subgraphs/remediation/test_replay.py
git commit -m "feat(remediation): add group replay + deterministic re-verification backstop"
```

---

## Task 8: Root deep agent + graph nodes

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Produces: `root_deepagent_node(state, config) -> dict`,
  `group_and_verify_gate(state, config) -> dict`,
  `route_after_group_verify(state) -> str`,
  `pr_and_persist_node(state, config) -> dict`. Consumed by Task 9
  (`graph.py`).
- Consumes: Task 3's `connected_groups`, Task 5's `RemediationDeepAgentState`,
  Task 6's `build_target_subagent`, Task 7's `apply_group_changes`/
  `replay_and_verify_group`, existing `selection.select_remediation_targets`,
  existing `npm_cli.npm_audit`/`npm_outdated`, existing
  `gh_cli_adapter`/`git_pr_port` (via `svc.get("git_pr")`, unchanged).

- [ ] **Step 1: Write the failing tests**

This task is integration-heavy (root deep agent orchestration); its own
unit tests mock the root agent's `ainvoke` and the DAO/container, proving
the node functions wire things correctly. Real end-to-end behavior against
actual `deepagents` machinery is Task 10's job.

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    group_and_verify_gate,
    pr_and_persist_node,
    root_deepagent_node,
    route_after_group_verify,
)
from src.models.conductor import FindingNote
from src.models.remediation import VerificationResult
from src.models.results import PrepResult


def _prep(**overrides):
    defaults = dict(
        id="prep-1", job_id="job-1", repo_path="/tmp/repo",
        docker_image="node:lts-alpine", detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "^4.17.11"}, "packages": {}},
        vector_store_id="",
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


@pytest.mark.asyncio
async def test_root_deepagent_node_seeds_targets_from_selection_and_invokes_agent():
    prep = _prep()
    analysis = MagicMock(findings=[FindingNote(dep_name="lodash", severity="high", description="d", evidence=[])])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    container = MagicMock()
    container.run = AsyncMock(return_value=(0, "{}", ""))
    config = {"configurable": {"result_dao": dao, "container": container}}

    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes._root_deep_agent"
    ) as mock_agent:
        mock_agent.ainvoke = AsyncMock(
            return_value={
                "remediations": {"lodash": {"target_dep": "lodash", "addresses": ["lodash"], "status": "skipped"}},
                "requires_edges": {},
            }
        )
        result = await root_deepagent_node(
            {"job_id": "job-1", "prep_result_id": "prep-1", "analysis_result_id": "a-1", "concern": "c"},
            config,
        )

    assert result["remediations"]["lodash"]["target_dep"] == "lodash"
    mock_agent.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_root_deepagent_node_no_targets_short_circuits():
    prep = _prep()
    analysis = MagicMock(findings=[])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    result = await root_deepagent_node(
        {"job_id": "job-1", "prep_result_id": "prep-1", "analysis_result_id": "a-1", "concern": "c"},
        config,
    )
    assert result["remediations"] == {}


@pytest.mark.asyncio
async def test_group_and_verify_gate_marks_group_fixed_on_green_verification():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    state = {
        "prep_result_id": "prep-1",
        "targets": {"lodash": {}},
        "remediations": {
            "lodash": {"id": "r1", "addresses": ["lodash"], "target_dep": "lodash", "strategy": "bump", "to_range": "^4.17.21", "status": "skipped"}
        },
        "requires_edges": {},
        "correction_rounds": 0,
    }
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
        AsyncMock(return_value=VerificationResult(installed=True, finding_resolved=True)),
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
            "lodash": {"id": "r1", "addresses": ["lodash"], "target_dep": "lodash", "strategy": "bump", "to_range": "^4.17.21", "status": "skipped"}
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
    assert "lodash" not in {k: v for k, v in result["remediations"].items() if v["status"] == "fixed"}


def test_route_after_group_verify_retries_then_finishes():
    assert route_after_group_verify({"retry_targets": ["lodash"]}) == "root_deepagent_node"
    assert route_after_group_verify({"retry_targets": []}) == "pr_and_persist_node"
    assert route_after_group_verify({}) == "pr_and_persist_node"


@pytest.mark.asyncio
async def test_pr_and_persist_node_skips_pr_when_consent_false():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep())
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    config = {"configurable": {"result_dao": dao, "container": MagicMock(), "remediate": False, "git_pr": git_pr}}

    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "lodash": {"id": "r1", "addresses": ["lodash"], "target_dep": "lodash", "strategy": "bump", "to_range": "^4.17.21", "status": "fixed"}
        },
        "requires_edges": {},
    }
    result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_not_called()
    assert result == {"remediation_result_id": "rid-1"}


@pytest.mark.asyncio
async def test_pr_and_persist_node_opens_one_pr_when_consent_true(tmp_path):
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=_prep(repo_path=str(tmp_path)))
    dao.save_remediation = AsyncMock(return_value="rid-1")
    git_pr = AsyncMock()
    git_pr.open_pr = AsyncMock(return_value="https://gh/pr/1")
    config = {"configurable": {"result_dao": dao, "container": MagicMock(), "remediate": True, "git_pr": git_pr}}
    (tmp_path / "package.json").write_text('{"dependencies": {"lodash": "^4.17.11"}}')

    state = {
        "job_id": "job-1",
        "prep_result_id": "prep-1",
        "remediations": {
            "lodash": {"id": "r1", "addresses": ["lodash"], "target_dep": "lodash", "strategy": "bump", "to_range": "^4.17.21", "status": "fixed"}
        },
        "requires_edges": {},
    }
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.copy_repo",
        return_value=str(tmp_path),
    ):
        result = await pr_and_persist_node(state, config)

    git_pr.open_pr.assert_awaited_once()
    assert result == {"remediation_result_id": "rid-1"}
```

Check `src/models/results.py::PrepResult` for its full required-field list
before finalizing `_prep()` above, same note as Task 6.

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Implement**

Create `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`:

```python
from __future__ import annotations

import logging

from deepagents import create_deep_agent
from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.deepagent.grouping import connected_groups
from src.main_graph.subgraphs.remediation.deepagent.replay import (
    apply_group_changes,
    replay_and_verify_group,
)
from src.main_graph.subgraphs.remediation.deepagent.state import RemediationDeepAgentState
from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import build_target_subagent
from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.main_graph.tools.npm_cli import npm_audit, npm_outdated
from src.models.remediation import Remediation, RemediationResult
from src.utils.config import settings
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_RECURSION_LIMIT = 50
_MAX_CORRECTION_ROUNDS = 2
_MAX_GROUPS = 20


def _build_root_deep_agent():
    return create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        tools=[],
        subagents=[build_target_subagent()],
        system_prompt=(
            "You coordinate dependency remediation for a Node.js project. "
            "For each open target listed in the first message, call the "
            "remediate_target tool, describing the dependency by name. If "
            "a call's result says another dependency is also required, "
            "dispatch that dependency too, unless it is already open or "
            "already remediated. Stop once every target you know about "
            "has been dispatched."
        ),
        state_schema=RemediationDeepAgentState,
    )


_root_deep_agent = _build_root_deep_agent()


async def root_deepagent_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    retry_targets = state.get("retry_targets")
    if retry_targets:
        targets = {dep: t for dep, t in (state.get("targets") or {}).items() if dep in retry_targets}
        evidence = state.get("evidence") or {}
    else:
        analysis = await dao.get_analysis(state["analysis_result_id"])
        initial = select_remediation_targets(
            analysis.findings, prep.dependency_graph, settings.risk_min_severity
        )
        targets = {t.target_dep: t.model_dump() for t in initial}
        if not targets:
            return {"targets": {}, "remediations": {}, "requires_edges": {}}
        audit = await npm_audit(
            prep.repo_path, container, prep.docker_image, prep.detected_package_manager
        )
        outdated = await npm_outdated(prep.repo_path, container, prep.docker_image)
        evidence = {"audit": audit, "outdated": outdated}

    if not targets:
        return {"targets": {}, "remediations": {}, "requires_edges": {}}

    open_list = "\n".join(
        f"- {dep} (addresses: {', '.join(t['addresses']) or 'none'})" for dep, t in targets.items()
    )
    initial_state = {
        "messages": [{"role": "user", "content": f"Open targets:\n{open_list}"}],
        "job_id": state["job_id"],
        "prep_result_id": state["prep_result_id"],
        "evidence": evidence,
        "targets": targets,
        "remediations": {},
        "requires_edges": {},
    }
    run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
    result = await _root_deep_agent.ainvoke(initial_state, run_config)

    return {
        "targets": targets,
        "evidence": evidence,
        "remediations": result.get("remediations") or {},
        "requires_edges": result.get("requires_edges") or {},
    }


def _is_green(v) -> bool:
    return v.installed and v.built is not False and v.tested is not False and v.finding_resolved is not False


async def group_and_verify_gate(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
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

    for group in groups[:_MAX_GROUPS]:
        members_dicts = [remediations[dep] for dep in group if dep in remediations]
        if len(members_dicts) != len(group):
            for member_dict in members_dicts:
                member_dict["status"] = "failed"
                member_dict["skip_reason"] = member_dict.get("skip_reason") or (
                    "a sibling dependency in this group was never dispatched"
                )
                member_dict["required_by"] = sorted(required_by_map.get(member_dict["target_dep"], []))
                settled[member_dict["target_dep"]] = member_dict
            continue

        members = [Remediation(**m) for m in members_dicts]
        verification = await replay_and_verify_group(
            members, prep.repo_path, container, prep.docker_image, prep.detected_package_manager
        )
        group_ok = _is_green(verification)
        for member_dict, member in zip(members_dicts, members, strict=True):
            member_dict["verification"] = verification.model_dump()
            member_dict["required_by"] = sorted(required_by_map.get(member.target_dep, []))
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
                settled[dep] = remediations[dep]

    if retry_targets:
        return {"remediations": settled, "retry_targets": retry_targets, "correction_rounds": correction_rounds + 1}
    return {"remediations": settled, "retry_targets": []}


def route_after_group_verify(state: RemediationState) -> str:
    return "root_deepagent_node" if state.get("retry_targets") else "pr_and_persist_node"


def _pr_title_and_body(group_remediations: list[Remediation]) -> tuple[str, str]:
    strategies = {r.strategy for r in group_remediations}
    if "replace" in strategies:
        label = "replace - review required"
    elif "bump_with_codemod" in strategies:
        label = "codemod - review required"
    else:
        label = "bump"
    deps = ", ".join(sorted(r.target_dep for r in group_remediations))
    title = f"Remediate {deps} ({label})"
    lines = [f"Automated dependency remediation - {label} (verified in sandbox):", ""]
    for r in group_remediations:
        if r.strategy == "replace":
            change = f"replace with {r.replacement_dep} {r.replacement_range}"
        else:
            change = f"{r.from_range} -> {r.to_range}"
        addresses = f" (fixes: {', '.join(r.addresses)})" if r.addresses else ""
        reason = f" (required by {', '.join(r.required_by)})" if r.required_by else ""
        lines.append(f"- {r.target_dep}: {change}{addresses}{reason}")
        if r.migration_plan:
            lines.append(f"  migration notes: {r.migration_plan}")
    return title, "\n".join(lines)


async def pr_and_persist_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")
    prep = await dao.get_prep(state["prep_result_id"])

    remediations = {dep: Remediation(**r) for dep, r in (state.get("remediations") or {}).items()}
    requires_edges = state.get("requires_edges") or {}
    groups = connected_groups(list(remediations), requires_edges)

    for group in groups:
        members = [remediations[dep] for dep in group if dep in remediations]
        if not members or not all(m.status == "fixed" for m in members):
            continue
        if consent and git_pr:
            work_dir = copy_repo(prep.repo_path)
            if not await apply_group_changes(work_dir, members):
                logger.warning("pr_and_persist_node: replay failed for group %s, skipping PR", group)
                continue
            branch = f"remediation/{state['job_id'][:8]}-{group[0]}"
            title, body = _pr_title_and_body(members)
            try:
                pr_url = await git_pr.open_pr(work_dir, branch, title, body)
                for member in members:
                    member.branch = branch
                    member.pr_url = pr_url
            except Exception as exc:
                logger.warning("pr_and_persist_node: PR creation failed for group %s: %s", group, exc)

    result = RemediationResult(job_id=state["job_id"], remediations=list(remediations.values()), consent=consent)
    rid = await dao.save_remediation(result)
    return {"remediation_result_id": rid}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "feat(remediation): add root deep agent + group verification/PR graph nodes"
```

---

## Task 9: Wire the new graph, delete the old orchestrator

**Note (controller update, logged in the ledger):** `orchestrator.py`,
`nodes/remediate.py`, `test_orchestrator.py`, and `test_remediate_node.py`
were already deleted right after Task 1 landed, not as part of this task —
Task 1 deleted `RemediationDecision`/moved `branch`/`pr_url` off
`RemediationResult`, which broke the whole `src.main_graph` import chain
for every subsequent task's test suite, so the deletion (with a temporary
`_remediate_placeholder` node standing in for the real graph) had to move
earlier than planned. `graph.py` currently builds a single-node placeholder
graph (`_remediate_placeholder`, raises `NotImplementedError`) — this task
still needs to REPLACE that placeholder with the real wiring below, exactly
as originally planned; only the *deletion* step already happened.

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/graph.py`
  (replace the placeholder with the real wiring)

**Interfaces:**
- Produces: `build_remediation_subgraph()` (rewired, using the already-extended
  `RemediationState` from Task 5 — this task does not touch `state.py`).
  This is the subgraph's external contract with the main graph — it must
  remain `{job_id, concern, prep_result_id, analysis_result_id}` in,
  `{remediation_result_id}` out (spec D1); confirm
  `apps/backend/src/main_graph/graph.py`'s wiring of the remediation
  subgraph needs no changes (it shouldn't — check it, don't just assume).

- [ ] **Step 1: Confirm nothing references the placeholder or the deleted files**

Run:
```bash
cd apps/backend && grep -rn "_remediate_placeholder\|from src.main_graph.subgraphs.remediation.orchestrator\|nodes.remediate import" src tests
```
Expected: `_remediate_placeholder` matches only in `graph.py` (which this
task rewrites); no matches at all for the other two patterns (both files
are already gone).

- [ ] **Step 2: Rewrite graph.py**

Replace `apps/backend/src/main_graph/subgraphs/remediation/graph.py`:

```python
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    group_and_verify_gate,
    pr_and_persist_node,
    root_deepagent_node,
    route_after_group_verify,
)
from src.main_graph.subgraphs.remediation.state import RemediationState


def build_remediation_subgraph():
    builder = StateGraph(RemediationState)
    builder.add_node("root_deepagent_node", root_deepagent_node)
    builder.add_node("group_and_verify_gate", group_and_verify_gate)
    builder.add_node("pr_and_persist_node", pr_and_persist_node)
    builder.add_edge(START, "root_deepagent_node")
    builder.add_edge("root_deepagent_node", "group_and_verify_gate")
    builder.add_conditional_edges(
        "group_and_verify_gate",
        route_after_group_verify,
        {
            "root_deepagent_node": "root_deepagent_node",
            "pr_and_persist_node": "pr_and_persist_node",
        },
    )
    builder.add_edge("pr_and_persist_node", END)
    return builder.compile()
```

- [ ] **Step 3: Confirm the main graph needs no change**

Run: `cd apps/backend && grep -n "remediation" src/main_graph/graph.py`
Expected: the remediation subgraph is wired by calling
`build_remediation_subgraph()` and passing `{job_id, concern,
prep_result_id, analysis_result_id}` in — same contract as before, so no
edit needed here. If the actual wiring differs from this expectation, fix
`graph.py` to match the new subgraph's unchanged external contract; do not
change the contract itself.

- [ ] **Step 4: Run the full backend suite**

Run: `cd apps/backend && uv run pytest -x -q`
Expected: PASS (aside from any test files this task hasn't reached yet —
by this task, everything through Task 8 should be green; any remaining
red should only be Task 10's not-yet-written integration test, which
doesn't exist yet at this point).

Run: `cd apps/backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/graph.py
git commit -m "feat(remediation): wire real deepagent tier-ladder graph, replace placeholder"
```

---

## Task 10: Blackbox integration test suite

**Files:**
- Create: `apps/backend/tests/subgraphs/test_remediation_subgraph.py`

**Interfaces:**
- Consumes: `apps/backend/tests/subgraphs/conftest.py`'s `result_dao`/
  `subgraph_config` fixtures (real MongoDB testcontainer — unmodified),
  `build_remediation_subgraph()` from Task 9, real `deepagents` machinery
  with a scripted fake chat model.

**Before writing the full suite**, run a small standalone spike (a bare
`create_deep_agent(model=<your fake>, tools=[], response_format=SomeSmallModel)`
invocation, no subagents) to confirm exactly how your fake chat model must
respond to make `structured_response` populate — this determines whether
your fake model needs to emit a special tool call or a final structured
message. Read `apps/backend/tests/subgraphs/test_analysis_subgraph.py`
first for the existing fake-chat-model pattern used in this codebase
(`bind_tools` override, content-routed responses) and extend it, rather
than inventing a different testing approach. Report what you found in
your task report — this is exactly the kind of "verify against the real
library" step this plan's sibling (the analysis-subgraph swap) already
established as required practice here, not optional.

This task's tests need real `gh`/`git`/container calls kept out of the
loop — use `subgraph_config`'s container mock (already returns success)
and inject a fake `git_pr`/`remediate` into `configurable`, and patch
`asyncio.create_subprocess_exec` at the `deepagent.tools` module path
(Task 4) so `read_release_notes` doesn't hit the real network — return a
harmless "no releases" response by default so it never blocks a test that
isn't specifically exercising release-notes content.

- [ ] **Step 1: Write the tests**

Cover, at minimum (adapt exact fixture/model wiring to what your spike in
the note above reveals; these are the required *behaviors*, not literal
code to paste unmodified):

```python
"""Blackbox integration tests for the deepagent-based remediation subgraph.
Requires Docker. Run with: uv run pytest tests/subgraphs/test_remediation_subgraph.py -v
"""
from __future__ import annotations

# ... imports: pytest, the fake chat model pattern from
# test_analysis_subgraph.py, build_remediation_subgraph, a PrepResult /
# AnalysisResult / FindingNote fixture factory, patch for
# asyncio.create_subprocess_exec and container.run.


@pytest.mark.asyncio
async def test_pure_bump_target_ships_one_fixed_pr(result_dao, subgraph_config):
    """Tier 0/1 regression: a single, uncoupled target with no requires
    signal verifies green and produces exactly one PR labeled "bump"."""


@pytest.mark.asyncio
async def test_requires_signal_pulls_in_a_non_finding_companion(result_dao, subgraph_config):
    """The eslint/eslint-plugin-react scenario from the spec: target A's
    subagent reports requires=["B"], B has no FindingNote, the root
    dispatches B, and both end up in ONE group/PR with B's
    Remediation.required_by == ["A"]."""


@pytest.mark.asyncio
async def test_parallel_task_calls_in_one_turn_do_not_crash_root_state(result_dao, subgraph_config):
    """Drive the real graph with a fake root model that emits TWO tool
    calls to remediate_target in a single turn (two independent, uncoupled
    targets). Assert both targets' Remediation records land correctly in
    the final state - proves RemediationDeepAgentState's reducers survive
    concurrent writes in one superstep, the exact bug class the
    analysis-subgraph swap hit and fixed."""


@pytest.mark.asyncio
async def test_correction_round_retries_then_gives_up_at_cap(result_dao, subgraph_config):
    """A target whose verification always fails: assert
    group_and_verify_gate retries it up to _MAX_CORRECTION_ROUNDS, then
    ships status="failed" with a reason, and that root_deepagent_node was
    invoked exactly (1 + _MAX_CORRECTION_ROUNDS) times, not more."""


@pytest.mark.asyncio
async def test_consent_false_opens_zero_prs_across_every_group(result_dao, subgraph_config):
    """Two independent fixed targets, remediate=False in configurable:
    assert the fake git_pr's open_pr is never called, and both
    Remediation records still have branch=None, pr_url=None, and a
    non-empty patch/verification result."""
```

- [ ] **Step 2: Run**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_remediation_subgraph.py -v`
(requires Docker; skips automatically if unavailable, per `conftest.py`)
Expected: PASS. Iterate on the fake model's scripted responses until all
five pass — this is real integration surface, expect to spend real
iteration cycles here, same as the analysis-subgraph swap's equivalent
task did.

- [ ] **Step 3: Run the full backend suite one more time**

Run: `cd apps/backend && uv run pytest -x -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: everything green.

- [ ] **Step 4: Commit**

```bash
git add apps/backend/tests/subgraphs/test_remediation_subgraph.py
git commit -m "test(remediation): add blackbox deepagent tier-ladder integration suite"
```
