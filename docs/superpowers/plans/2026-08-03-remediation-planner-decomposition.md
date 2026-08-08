# Remediation Planner Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape the remediation subgraph front half from a monolithic per-target deep agent into an explicit `classify → investigate → plan_and_orchestrate → verify → pr` pipeline, with a persisted, reviewable `MigrationPlan` per target.

**Architecture:** A guaranteed deterministic investigation phase (Dependency + Source deterministic, Release = version-scoped changelog fetch + one LLM digest) feeds a single deepagent that plans (emits a structured `MigrationPlan` via a mandatory `commit_plan` tool) and delegates to typed scoped implementation agents (bump = deterministic, codemod = sandboxed deepagent, replace = stubbed for Spec A). The deterministic verify/replay/PR backstop is retained unchanged.

**Tech Stack:** Python 3.12, LangGraph, `deepagents`, Pydantic, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-02-remediation-planner-decomposition-design.md`

## Global Constraints

- **Deterministic backstop untouched (spec D8):** `group_and_verify_gate`, `replay_and_verify_group`, `apply_group_changes`, and `pr_and_persist_node`'s PR/consent flow keep current behavior. `group_and_verify_gate` remains the ONLY thing that sets a shipped `Remediation.status`; every implementation agent self-report stays provisional.
- **Honest bounds (spec D9):** recursion limit, correction-round cap, group cap all degrade into `skipped`/`failed` WITH reasons, never crash. A malformed/absent `MigrationPlan` degrades that target to `failed` with a reason.
- **Tier is an advisory hint, not a gate (spec D1):** no target is settled from classification alone; every selected target flows to investigation + planning.
- **Uniform planning path (spec D6):** every target (including a clean r1 bump) goes through the planning deepagent and gets a persisted `MigrationPlan`; `bump` tasks EXECUTE deterministically. No r1 short-circuit is built.
- **Replacement stubbed in Spec A (spec D4):** the `replace` task kind and routing exist, but `replace` work settles as deferred/skipped — no real migration. Full r3 is Spec B.
- **Release investigator target version (spec soft-dep):** use the finding's `fixed_version` when present; until version-enrichment lands, fall back to latest-satisfying-stable resolved via `npm view`.
- **LLM model:** reuse `get_llm(Model.GPT_5_4_MINI)`, matching the existing subgraph.
- **Package manager, container, DAO** come from `get_services(config)` (`result_dao`, `container`), exactly as current nodes do. No emoji anywhere.

## File Structure

- `src/models/remediation.py` (modify) — add `ReleaseDigest`, `TargetInvestigation`, `MigrationTask`, `MigrationPlan`; add `tier` to `RemediationTarget` and `plan` to `Remediation`.
- `src/main_graph/subgraphs/remediation/changelog.py` (modify) — add semver-windowed release fetch alongside the existing full fetch.
- `src/main_graph/subgraphs/remediation/investigate.py` (create) — the three investigators, `investigate_target`, and `investigate_node`.
- `src/main_graph/subgraphs/remediation/classify.py` (modify) — tier becomes a hint carried on the target; drop the r3-settle branch.
- `src/main_graph/subgraphs/remediation/state.py` (modify) — add `investigations`, `migration_plans` channels.
- `src/main_graph/subgraphs/remediation/deepagent/state.py` (modify) — add `migration_plans` channel for the `commit_plan` tool to write into.
- `src/main_graph/subgraphs/remediation/deepagent/tools.py` (modify) — add `make_commit_plan_tool`.
- `src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py` (rewrite) — replace the monolithic `remediate_target` with typed `codemod-adapter` (+ `replacement-migrator` stub) subagents.
- `src/main_graph/subgraphs/remediation/deepagent/nodes.py` (modify) — reshape `root_deepagent_node` into the plan-and-orchestrate node; persist the plan in `pr_and_persist_node`.
- `src/main_graph/subgraphs/remediation/graph.py` (modify) — insert `investigate_node`.

---

### Task 1: Data models for investigation and plan

**Files:**
- Modify: `src/models/remediation.py`
- Test: `tests/unit/models/test_remediation_models.py`

**Interfaces:**
- Produces: `ReleaseDigest`, `TargetInvestigation`, `MigrationTask`, `MigrationPlan` pydantic models; `RemediationTarget.tier: Literal["r1","r2","r3"] | None = None`; `Remediation.plan: MigrationPlan | None = None`. Every later task consumes these exact names/types.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/models/test_remediation_models.py`:

```python
from src.models.remediation import (
    MigrationPlan,
    MigrationTask,
    ReleaseDigest,
    Remediation,
    RemediationTarget,
    TargetInvestigation,
)


def test_release_digest_defaults():
    d = ReleaseDigest(from_version="1.0.0", to_version="2.0.0", migration_needed=True)
    assert d.migration_guide == ""
    assert d.breaking_changes == []


def test_target_investigation_round_trip():
    inv = TargetInvestigation(
        target_dep="lodash",
        dependents=["a"],
        call_sites=["src/x.ts"],
        release=ReleaseDigest(from_version=None, to_version=None, migration_needed=False),
    )
    assert TargetInvestigation(**inv.model_dump()).call_sites == ["src/x.ts"]


def test_migration_plan_defaults_and_task():
    plan = MigrationPlan(
        target_dep="lodash",
        tier_hint="r2",
        tasks=[MigrationTask(kind="bump", rationale="patch", to_range="^4.17.21")],
    )
    assert plan.requires == []
    assert plan.migration_guide == ""
    assert plan.tasks[0].kind == "bump"


def test_remediation_target_carries_tier():
    t = RemediationTarget(target_dep="lodash", addresses=["lodash"], tier="r1")
    assert t.tier == "r1"
    assert RemediationTarget(target_dep="x", addresses=[]).tier is None


def test_remediation_embeds_plan():
    plan = MigrationPlan(target_dep="lodash", tier_hint="r1", tasks=[])
    r = Remediation(addresses=[], target_dep="lodash", plan=plan)
    assert r.plan.target_dep == "lodash"
    assert Remediation(addresses=[], target_dep="x").plan is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -v -k "digest or investigation or migration_plan or carries_tier or embeds_plan"`
Expected: FAIL — `ImportError` for `ReleaseDigest`/`TargetInvestigation`/`MigrationTask`/`MigrationPlan`, and `tier`/`plan` not valid fields.

- [ ] **Step 3: Add the models**

In `src/models/remediation.py`, add after the existing imports (`Literal` is already imported) and models:

```python
class ReleaseDigest(BaseModel):
    """Release investigator output for one target."""

    from_version: str | None
    to_version: str | None
    migration_needed: bool  # False => clean bump, no code change
    migration_guide: str = ""  # LLM prose; "" when not needed
    breaking_changes: list[str] = Field(default_factory=list)


class TargetInvestigation(BaseModel):
    """Everything the Migration Planner reads about one target."""

    target_dep: str
    dependents: list[str] = Field(default_factory=list)
    call_sites: list[str] = Field(default_factory=list)
    release: ReleaseDigest


class MigrationTask(BaseModel):
    kind: Literal["bump", "codemod", "replace"]
    rationale: str
    to_range: str | None = None
    files: list[str] = Field(default_factory=list)
    replacement_dep: str | None = None
    replacement_range: str | None = None


class MigrationPlan(BaseModel):
    target_dep: str
    tier_hint: Literal["r1", "r2", "r3"]
    migration_guide: str = ""
    tasks: list[MigrationTask] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
```

Add `tier` to `RemediationTarget`:

```python
class RemediationTarget(BaseModel):
    """Internal: a deduped unit of work produced by target selection."""

    target_dep: str
    addresses: list[str]
    current_range: str | None = None
    tier: Literal["r1", "r2", "r3"] | None = None  # advisory hint from classify
```

Add `plan` to `Remediation` (after the existing `migration_plan: str = ""` field):

```python
    plan: MigrationPlan | None = None  # persisted, reviewable (spec D5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -v`
Expected: PASS (new tests + all pre-existing model tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/models/remediation.py apps/backend/tests/unit/models/test_remediation_models.py
git commit -m "feat: add investigation/plan models for remediation decomposition"
```

---

### Task 2: Version-scoped changelog fetch

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/changelog.py`
- Test: `tests/unit/subgraphs/remediation/test_changelog.py`

**Interfaces:**
- Consumes: existing `fetch_release_notes` (unchanged, still used by `classify.py`).
- Produces: `fetch_release_notes_between(package_name, from_version, to_version, repo_path, container, docker_image) -> dict` returning the same shape as `fetch_release_notes` but with `releases` filtered to semver tags in the half-open window `(from_version, to_version]`. When `from_version`/`to_version` is `None` or unparseable, it returns the unfiltered recent set (honest degradation, never an empty false negative). Task 3 consumes this.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/subgraphs/remediation/test_changelog.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.remediation.changelog import (
    _tag_version,
    _tag_in_window,
    fetch_release_notes_between,
)


def test_tag_version_strips_v_prefix():
    assert _tag_version("v4.17.21") == (4, 17, 21)
    assert _tag_version("4.17.21") == (4, 17, 21)
    assert _tag_version("release-1.2") is None


def test_tag_in_window_half_open():
    # (1.0.0, 2.0.0]: excludes current, includes target
    assert _tag_in_window("v1.0.0", (1, 0, 0), (2, 0, 0)) is False
    assert _tag_in_window("v1.5.0", (1, 0, 0), (2, 0, 0)) is True
    assert _tag_in_window("v2.0.0", (1, 0, 0), (2, 0, 0)) is True
    assert _tag_in_window("v2.0.1", (1, 0, 0), (2, 0, 0)) is False


@pytest.mark.asyncio
async def test_fetch_between_filters_to_window():
    full = {
        "package_name": "lodash",
        "available": True,
        "repository": "lodash/lodash",
        "releases": [
            {"tag": "v2.0.0", "name": "2", "body": "b"},
            {"tag": "v1.5.0", "name": "1.5", "body": "b"},
            {"tag": "v1.0.0", "name": "1", "body": "b"},
        ],
    }
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.fetch_release_notes",
        AsyncMock(return_value=full),
    ):
        out = await fetch_release_notes_between(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", None, "img"
        )
    tags = [r["tag"] for r in out["releases"]]
    assert tags == ["v2.0.0", "v1.5.0"]  # v1.0.0 excluded (half-open)


@pytest.mark.asyncio
async def test_fetch_between_unparseable_bounds_returns_unfiltered():
    full = {
        "package_name": "lodash",
        "available": True,
        "releases": [{"tag": "v1.5.0", "name": "x", "body": "b"}],
    }
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.fetch_release_notes",
        AsyncMock(return_value=full),
    ):
        out = await fetch_release_notes_between(
            "lodash", None, None, "/tmp/repo", None, "img"
        )
    assert out["releases"] == full["releases"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_changelog.py -v -k "tag or between"`
Expected: FAIL — `ImportError` for `_tag_version`/`_tag_in_window`/`fetch_release_notes_between`.

- [ ] **Step 3: Implement the window filter**

In `src/main_graph/subgraphs/remediation/changelog.py`, add near the top (after `_GITHUB_REPO_RE`):

```python
_SEMVER_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def _tag_version(tag: str | None) -> tuple[int, int, int] | None:
    if not tag:
        return None
    match = _SEMVER_TAG_RE.match(tag.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _tag_in_window(
    tag: str | None,
    low: tuple[int, int, int],
    high: tuple[int, int, int],
) -> bool:
    """Half-open (low, high]: exclude the installed version, include target."""
    v = _tag_version(tag)
    if v is None:
        return False
    return low < v <= high
```

Then add the windowed fetch at the end of the file:

```python
async def fetch_release_notes_between(
    package_name: str,
    from_version: str | None,
    to_version: str | None,
    repo_path: str,
    container,
    docker_image: str,
) -> dict:
    """Like fetch_release_notes, but keep only releases whose tag falls in the
    half-open window (from_version, to_version]. When either bound is missing
    or unparseable, return the unfiltered recent set (honest degradation)."""
    full = await fetch_release_notes(package_name, repo_path, container, docker_image)
    if not full.get("available"):
        return full
    low = _tag_version(from_version)
    high = _tag_version(to_version)
    if low is None or high is None:
        return full
    windowed = [r for r in full.get("releases", []) if _tag_in_window(r.get("tag"), low, high)]
    return {**full, "releases": windowed}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_changelog.py -v`
Expected: PASS (new + existing changelog tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/changelog.py apps/backend/tests/unit/subgraphs/remediation/test_changelog.py
git commit -m "feat: add version-scoped changelog fetch for release investigator"
```

---

### Task 3: Release investigator (digest)

**Files:**
- Create: `src/main_graph/subgraphs/remediation/investigate.py`
- Test: `tests/unit/subgraphs/remediation/test_investigate.py`

**Interfaces:**
- Consumes: `fetch_release_notes_between` (Task 2), `ReleaseDigest` (Task 1).
- Produces: `async def investigate_release(target_dep, from_version, to_version, repo_path, container, docker_image) -> ReleaseDigest`. On any failure it returns a `ReleaseDigest(migration_needed=True, ...)` conservatively (assume breaking, force planning attention) with an explanatory `breaking_changes` entry — never crashes. Task 4 consumes it.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/subgraphs/remediation/test_investigate.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.investigate import investigate_release
from src.models.remediation import ReleaseDigest


@pytest.mark.asyncio
async def test_investigate_release_returns_digest_from_llm():
    notes = {"available": True, "releases": [{"tag": "v2.0.0", "body": "removed foo()"}]}
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_investigate.py -v -k release`
Expected: FAIL — module `investigate` does not exist yet.

- [ ] **Step 3: Implement the release investigator**

Create `src/main_graph/subgraphs/remediation/investigate.py`:

```python
"""Investigation phase (spec D2): deterministic Dependency + Source
investigators and an LLM-digested Release investigator, combined per target
into a TargetInvestigation that the plan_and_orchestrate deepagent reads."""

from __future__ import annotations

import json
import logging
from typing import cast

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes_between
from src.models.remediation import ReleaseDigest
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_RELEASE_SYSTEM_PROMPT = """\
You are the release investigator for an npm dependency upgrade. Given the \
GitHub release notes for the versions BETWEEN the installed version and the \
target version, decide whether upgrading requires any code change in a \
consumer, and if so produce a concise migration guide.

Set migration_needed=true ONLY when the notes describe a breaking change a \
typical consumer would have to adapt to (removed/renamed API, changed \
default, new required config, etc.). A pure bug/patch/feature release with \
no consumer-facing break is migration_needed=false with an empty guide. \
List each concrete breaking change in breaking_changes. Keep migration_guide \
short and specific to what a caller must change."""


async def investigate_release(
    target_dep: str,
    from_version: str | None,
    to_version: str | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> ReleaseDigest:
    try:
        notes = await fetch_release_notes_between(
            target_dep, from_version, to_version, repo_path, container, docker_image
        )
        structured = _llm.with_structured_output(
            ReleaseDigest, method="function_calling"
        )
        digest = cast(
            ReleaseDigest,
            await structured.ainvoke(
                [
                    {"role": "system", "content": _RELEASE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Dependency: {target_dep}\n"
                            f"From version: {from_version or 'unknown'}\n"
                            f"To version: {to_version or 'unknown'}\n"
                            f"Release notes: {json.dumps(notes)[:6000]}"
                        ),
                    },
                ]
            ),
        )
        # Trust the LLM's decision but pin the versions to what we asked about.
        digest.from_version = from_version
        digest.to_version = to_version
        return digest
    except Exception as exc:
        logger.warning(
            "investigate_release: failed for %s (%s->%s): %s; assuming breaking",
            target_dep,
            from_version,
            to_version,
            exc,
        )
        return ReleaseDigest(
            from_version=from_version,
            to_version=to_version,
            migration_needed=True,
            migration_guide="",
            breaking_changes=[f"release investigation failed, assuming breaking: {exc}"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_investigate.py -v -k release`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/investigate.py apps/backend/tests/unit/subgraphs/remediation/test_investigate.py
git commit -m "feat: add LLM release investigator producing ReleaseDigest"
```

---

### Task 4: Deterministic investigators + investigate_target

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/investigate.py`
- Test: `tests/unit/subgraphs/remediation/test_investigate.py`

**Interfaces:**
- Consumes: `dependents_of` (`discovery/dependency_graph.py`), `find_local_usage_sites` (`tools/search_code.py`), `investigate_release` (Task 3), `RemediationTarget`/`TargetInvestigation` (Task 1).
- Produces:
  - `def investigate_dependents(dependency_graph, target_dep) -> list[str]`
  - `def investigate_call_sites(repo_path, target_dep) -> list[str]`
  - `def _resolve_versions(target, dependency_graph) -> tuple[str | None, str | None]` returning `(from_version, to_version)` — `from` = installed version from the graph's `direct`/`packages`; `to` = `None` for now (release fetch degrades to unfiltered recent, honest until version-enrichment lands).
  - `async def investigate_target(target, repo_path, dependency_graph, container, docker_image) -> TargetInvestigation`. Task 5 consumes `investigate_target`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/subgraphs/remediation/test_investigate.py`:

```python
from src.main_graph.subgraphs.remediation.investigate import (
    investigate_call_sites,
    investigate_dependents,
    investigate_target,
)
from src.models.remediation import RemediationTarget, TargetInvestigation


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_investigate.py -v -k "dependents or call_sites or combines"`
Expected: FAIL — `ImportError` for the three new names.

- [ ] **Step 3: Implement the deterministic investigators**

Add imports at the top of `investigate.py`:

```python
from src.main_graph.subgraphs.discovery.dependency_graph import dependents_of
from src.main_graph.subgraphs.remediation.workspace import  # noqa: F401  (none needed)
from src.main_graph.tools.search_code import find_local_usage_sites
from src.models.remediation import RemediationTarget, TargetInvestigation
```

(Drop the unused `workspace` import line — shown only to flag no workspace dependency here.) Then append:

```python
def investigate_dependents(dependency_graph: dict, target_dep: str) -> list[str]:
    """Deterministic Dependency investigator: packages in the tree that
    depend on target_dep (structural, from the resolved graph)."""
    return dependents_of(dependency_graph, target_dep)


def investigate_call_sites(repo_path: str, target_dep: str) -> list[str]:
    """Deterministic Source investigator: repo files that mention target_dep,
    sorted. Reuses the local substring scan (no container, no LLM)."""
    return sorted(
        {hit["file"] for hit in find_local_usage_sites(repo_path, target_dep)}
    )


def _resolve_versions(
    target: RemediationTarget, dependency_graph: dict
) -> tuple[str | None, str | None]:
    """(from_version, to_version). from = installed version from the graph;
    to = None until analysis-finding version-enrichment supplies fixed_version
    (release fetch then degrades to the unfiltered recent set, spec soft-dep)."""
    installed = (dependency_graph.get("direct") or {}).get(target.target_dep)
    return (installed, None)


async def investigate_target(
    target: RemediationTarget,
    repo_path: str,
    dependency_graph: dict,
    container: ContainerRunPort,
    docker_image: str,
) -> TargetInvestigation:
    from_version, to_version = _resolve_versions(target, dependency_graph)
    release = await investigate_release(
        target.target_dep, from_version, to_version, repo_path, container, docker_image
    )
    return TargetInvestigation(
        target_dep=target.target_dep,
        dependents=investigate_dependents(dependency_graph, target.target_dep),
        call_sites=investigate_call_sites(repo_path, target.target_dep),
        release=release,
    )
```

Fix the import block: replace the placeholder `workspace` import line with nothing — the final imports are `dependents_of`, `find_local_usage_sites`, `RemediationTarget`, `TargetInvestigation` (the last two extend Task 3's existing `ReleaseDigest` import).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_investigate.py -v`
Expected: PASS (all release + deterministic tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/investigate.py apps/backend/tests/unit/subgraphs/remediation/test_investigate.py
git commit -m "feat: add deterministic dependency/source investigators and investigate_target"
```

---

### Task 5: investigate_node (per-target fan-out) + state channels

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/investigate.py`
- Modify: `src/main_graph/subgraphs/remediation/state.py`
- Test: `tests/unit/subgraphs/remediation/test_investigate.py`

**Interfaces:**
- Consumes: `investigate_target` (Task 4); `RemediationState`; `get_services`.
- Produces: `async def investigate_node(state, config) -> dict` returning `{"investigations": {target_dep: TargetInvestigation.model_dump()}}`, bounded to a fixed concurrency cap (reuse the value 6, matching `classify`'s `_MAX_CONCURRENT_CLASSIFICATIONS`, to avoid provider 429s — a documented recurring issue). Reads `state["targets"]` (each a `RemediationTarget.model_dump()`). `RemediationState` gains `investigations` and `migration_plans` channels. Task 8 reads `state["investigations"]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/subgraphs/remediation/test_investigate.py`:

```python
import asyncio


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
            release=ReleaseDigest(from_version=None, to_version=None, migration_needed=False),
        )

    with patch(
        "src.main_graph.subgraphs.remediation.investigate.investigate_target",
        AsyncMock(side_effect=_fake_investigate),
    ):
        out = await investigate_node(
            {"job_id": "j", "prep_result_id": "p", "targets": targets}, config
        )

    assert set(out["investigations"]) == set(deps)
    assert peak <= 6, f"expected cap 6, saw {peak}"
    assert peak > 1


@pytest.mark.asyncio
async def test_investigate_node_no_targets_short_circuits():
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=MagicMock())
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}
    out = await investigate_node({"job_id": "j", "prep_result_id": "p", "targets": {}}, config)
    assert out == {"investigations": {}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_investigate.py -v -k node`
Expected: FAIL — `investigate_node` not defined.

- [ ] **Step 3: Implement investigate_node and extend state**

Add to the top of `investigate.py`:

```python
import asyncio

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.state import RemediationState
```

Append to `investigate.py`:

```python
_MAX_CONCURRENT_INVESTIGATIONS = 6  # matches classify; guards against 429s


async def investigate_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    targets = state.get("targets") or {}
    if not targets:
        return {"investigations": {}}
    prep = await dao.get_prep(state["prep_result_id"])

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_INVESTIGATIONS)

    async def _bounded(target_dict: dict) -> TargetInvestigation:
        target = RemediationTarget(**target_dict)
        async with semaphore:
            return await investigate_target(
                target,
                prep.repo_path,
                prep.dependency_graph,
                container,
                prep.docker_image,
            )

    results = await asyncio.gather(*[_bounded(t) for t in targets.values()])
    return {"investigations": {inv.target_dep: inv.model_dump() for inv in results}}
```

In `src/main_graph/subgraphs/remediation/state.py`, add two channels to `RemediationState`:

```python
    investigations: NotRequired[Annotated[dict[str, dict], _merge_replace]]
    migration_plans: NotRequired[Annotated[dict[str, dict], _merge_replace]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_investigate.py -v`
Expected: PASS (all investigate tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/investigate.py apps/backend/src/main_graph/subgraphs/remediation/state.py apps/backend/tests/unit/subgraphs/remediation/test_investigate.py
git commit -m "feat: add investigate_node fan-out and state channels"
```

---

### Task 6: Classify — tier becomes a hint, drop the r3-settle gate

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/classify.py`
- Test: `tests/unit/subgraphs/remediation/test_classify.py`

**Interfaces:**
- Consumes: `select_remediation_targets`, `classify_target` (both unchanged), `RemediationTarget.tier` (Task 1).
- Produces: `classify_targets_node` now returns `{"targets": {dep: RemediationTarget(..., tier=<tier>).model_dump()}}` for EVERY selected target (including r3), and `{"remediations": {}}`. No target is settled from classification. Concurrency cap and no-findings short-circuit are unchanged. Task 5's `investigate_node` and Task 8's planner read `tier` off each target dict.

- [ ] **Step 1: Update the existing test to the new behavior**

In `tests/unit/subgraphs/remediation/test_classify.py`, replace `test_classify_targets_node_splits_r3_from_dispatchable_targets` with:

```python
@pytest.mark.asyncio
async def test_classify_targets_node_carries_tier_hint_no_r3_settle():
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "^4.17.11", "left-pad": "1.0.0"},
            "packages": {},
        }
    )
    analysis = MagicMock(
        findings=[
            FindingNote(dep_name="lodash", severity="high", description="d", evidence=[]),
            FindingNote(dep_name="left-pad", severity="high", description="d2", evidence=[]),
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    async def _fake_classify(target, repo_path, container, docker_image):
        if target.target_dep == "left-pad":
            return TargetClassification(tier="r3", rationale="abandoned")
        return TargetClassification(tier="r1", rationale="patch bump")

    with patch(
        "src.main_graph.subgraphs.remediation.classify.classify_target",
        AsyncMock(side_effect=_fake_classify),
    ):
        result = await classify_targets_node(
            {"job_id": "job-1", "prep_result_id": "prep-1",
             "analysis_result_id": "a-1", "concern": "c"},
            config,
        )

    # Every target flows through (r3 is NOT settled here anymore).
    assert set(result["targets"]) == {"lodash", "left-pad"}
    assert result["remediations"] == {}
    assert result["targets"]["left-pad"]["tier"] == "r3"
    assert result["targets"]["lodash"]["tier"] == "r1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_classify.py -v -k "carries_tier or bounds or no_findings"`
Expected: FAIL on `carries_tier` — current code settles r3 into `remediations` and does not attach `tier`.

- [ ] **Step 3: Rewrite the classification loop**

In `classify.py`, replace the loop at the end of `classify_targets_node` (the `for target, classification in zip(...)` block and its `Remediation`/`return`) with:

```python
    targets: dict[str, dict] = {}
    for target, classification in zip(initial, classifications, strict=True):
        target.tier = classification.tier
        targets[target.target_dep] = target.model_dump()

    return {"targets": targets, "remediations": {}}
```

Remove the now-unused `Remediation` import if nothing else in the file uses it (leave `RemediationTarget`). Keep the no-findings short-circuit returning `{"targets": {}, "remediations": {}}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_classify.py -v`
Expected: PASS (rewritten test + unchanged concurrency/no-findings tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/classify.py apps/backend/tests/unit/subgraphs/remediation/test_classify.py
git commit -m "feat: classify carries tier hint, drop r3-settle gate"
```

---

### Task 7: commit_plan tool + typed implementation subagents

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/deepagent/state.py`
- Modify: `src/main_graph/subgraphs/remediation/deepagent/tools.py`
- Rewrite: `src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py`
- Test: `tests/unit/subgraphs/remediation/test_deepagent_tools.py`, `tests/unit/subgraphs/remediation/test_subagent_wrapper.py`

**Interfaces:**
- Consumes: `MigrationPlan` (Task 1); `_merge_replace` (existing, `deepagent/state.py`); `make_bump_dependency_tool`/`make_verify_tool`/`make_read_release_notes_tool`/`make_blast_radius_tool`/`make_search_code_tool` (existing).
- Produces:
  - `RemediationDeepAgentState.migration_plans: Annotated[dict[str, dict], _merge_replace]`.
  - `make_commit_plan_tool()` → a `@tool commit_plan(plan: MigrationPlan)` that returns a langgraph `Command(update={"migration_plans": {plan.target_dep: plan.model_dump()}})`. The prompt requires calling it FIRST.
  - `build_codemod_subagent(work_dir, container, docker_image, package_manager)` and `build_replacement_subagent(...)` → `CompiledSubAgent` dicts (`name`, `description`, `runnable`). The replacement subagent is a Spec-A stub: it returns a `RemediationOutcome(status="skipped", skip_reason="dependency replacement deferred (Spec B)")` without editing.
- Task 8 dispatches these subagents and reads `result["migration_plans"]`.

- [ ] **Step 1: Write the failing test for commit_plan**

Add to `tests/unit/subgraphs/remediation/test_deepagent_tools.py`:

```python
import pytest
from langgraph.types import Command

from src.main_graph.subgraphs.remediation.deepagent.tools import make_commit_plan_tool
from src.models.remediation import MigrationPlan, MigrationTask


@pytest.mark.asyncio
async def test_commit_plan_writes_plan_to_state():
    tool = make_commit_plan_tool()
    plan = MigrationPlan(
        target_dep="lodash",
        tier_hint="r2",
        tasks=[MigrationTask(kind="bump", rationale="x", to_range="^4.17.21")],
    )
    result = await tool.ainvoke({"plan": plan})
    assert isinstance(result, Command)
    assert result.update["migration_plans"]["lodash"]["tier_hint"] == "r2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_tools.py -v -k commit_plan`
Expected: FAIL — `make_commit_plan_tool` not defined.

- [ ] **Step 3: Implement commit_plan and the migration_plans channel**

In `deepagent/state.py`, add to `RemediationDeepAgentState`:

```python
    migration_plans: Annotated[dict[str, dict], _merge_replace]
```

In `deepagent/tools.py`, add:

```python
from langgraph.types import Command

from src.models.remediation import MigrationPlan


def make_commit_plan_tool():
    @tool
    def commit_plan(plan: MigrationPlan) -> Command:
        """Record the migration plan for this target. Call this FIRST, before
        dispatching any implementation work. The plan is persisted for review."""
        return Command(
            update={"migration_plans": {plan.target_dep: plan.model_dump()}}
        )

    return commit_plan
```

- [ ] **Step 4: Run the commit_plan test**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_tools.py -v -k commit_plan`
Expected: PASS.

- [ ] **Step 5: Write the failing test for the typed subagents**

Replace `tests/unit/subgraphs/remediation/test_subagent_wrapper.py`'s contents with tests for the new builders (the old monolithic `remediate_target` is gone):

```python
from __future__ import annotations

from unittest.mock import MagicMock

from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_codemod_subagent,
    build_replacement_subagent,
)


def test_build_codemod_subagent_shape():
    sub = build_codemod_subagent("/tmp/work", MagicMock(), "img", "npm")
    assert sub["name"] == "codemod_adapter"
    assert "runnable" in sub and sub["description"]


def test_build_replacement_subagent_shape():
    sub = build_replacement_subagent("/tmp/work", MagicMock(), "img", "npm")
    assert sub["name"] == "replacement_migrator"
    assert "runnable" in sub and sub["description"]
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_subagent_wrapper.py -v`
Expected: FAIL — new builders not defined.

- [ ] **Step 7: Rewrite subagent_wrapper.py with the typed builders**

Replace `deepagent/subagent_wrapper.py` with builders for the two code-editing subagents. The `codemod_adapter` is a sandboxed deepagent (mirrors today's nested agent: `FilesystemBackend(root_dir=work_dir, virtual_mode=True)`, tools = release-notes/blast-radius/search-code/bump/verify, `response_format=RemediationOutcome`). The `replacement_migrator` is a Spec-A stub node.

```python
"""Typed implementation subagents dispatched by plan_and_orchestrate
(spec D4). codemod_adapter is a sandboxed deepagent that adapts call sites;
replacement_migrator is a Spec-A stub (real r3 is Spec B)."""

from __future__ import annotations

from deepagents import CompiledSubAgent, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_bump_dependency_tool,
    make_read_release_notes_tool,
    make_verify_tool,
)
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.search_code import make_search_code_tool
from src.models.remediation import RemediationOutcome
from src.utils.llm import Model, get_llm

_CODEMOD_PROMPT = """\
You adapt this Node.js project's own source to a dependency upgrade that has
a known breaking change. You are given the migration guide and the specific
files that use the dependency. Edit ONLY what the guide requires, then call
verify. Iterate until verify is green or you conclude there is no safe fix.
Finish with a structured RemediationOutcome including the unified diff of your
edits in code_diff and a short summary."""


def build_codemod_subagent(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
) -> CompiledSubAgent:
    tools = [
        make_read_release_notes_tool(work_dir, container, docker_image),
        make_blast_radius_tool(work_dir, container, docker_image),
        make_search_code_tool(work_dir, container, docker_image),
        make_bump_dependency_tool(work_dir),
        make_verify_tool(work_dir, container, docker_image, package_manager, []),
    ]
    agent = create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        tools=tools,
        system_prompt=_CODEMOD_PROMPT,
        backend=FilesystemBackend(root_dir=work_dir, virtual_mode=True),
        response_format=RemediationOutcome,
    )
    return {
        "name": "codemod_adapter",
        "description": (
            "Adapt this project's call sites to a breaking dependency change. "
            "Give it the migration guide and the affected files."
        ),
        "runnable": agent,
    }


class _StubState(TypedDict):
    messages: list


async def _replacement_stub(state: _StubState, config: RunnableConfig) -> dict:
    outcome = RemediationOutcome(
        strategy="replace",
        status="skipped",
        skip_reason="dependency replacement deferred (Spec B)",
        summary="replacement not implemented in this build",
    )
    return {"messages": [], "structured_response": outcome}


def build_replacement_subagent(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
) -> CompiledSubAgent:
    graph = StateGraph(_StubState)
    graph.add_node("run", _replacement_stub)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return {
        "name": "replacement_migrator",
        "description": (
            "Replace a dependency with a different package and migrate usage. "
            "Deferred in this build; reports skipped."
        ),
        "runnable": graph.compile(),
    }
```

- [ ] **Step 8: Run to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_subagent_wrapper.py tests/unit/subgraphs/remediation/test_deepagent_tools.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/state.py apps/backend/src/main_graph/subgraphs/remediation/deepagent/tools.py apps/backend/src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_tools.py apps/backend/tests/unit/subgraphs/remediation/test_subagent_wrapper.py
git commit -m "feat: add commit_plan tool and typed codemod/replacement subagents"
```

---

### Task 8: plan_and_orchestrate node

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/deepagent/nodes.py`
- Test: `tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Consumes: `state["targets"]` (with `tier`), `state["investigations"]` (Task 5), the typed subagents + `make_commit_plan_tool` (Task 7), `MigrationPlan`/`Remediation`/`RemediationOutcome` (Task 1 + existing).
- Produces: `root_deepagent_node` reshaped — per target it invokes ONE planning deepagent (built with `subagents=[codemod, replacement]` + `tools=[commit_plan]`) seeded with the investigation evidence; the deepagent calls `commit_plan` first, then dispatches. The node returns `{"targets", "remediations", "requires_edges", "migration_plans"}`. Each `Remediation` gets `plan` set from the committed `MigrationPlan`; `bump`-only plans are executed by applying the bump (no code diff) — a `Remediation(strategy="bump", to_range=...)`. Honest bounds: recursion-limit / malformed-plan → that target's `Remediation(status="failed", skip_reason=...)`. `group_and_verify_gate`/`route_after_group_verify` stay as-is (they read `remediations`/`requires_edges`, unchanged).

**Note on scope:** this task keeps today's retry mechanism (`route_after_group_verify` re-enters this node with `retry_targets`) intact. The node's per-target loop uses `retry_targets` when present (same branch as today), else all `targets`.

- [ ] **Step 1: Write the failing test**

Replace the dispatch tests in `tests/unit/subgraphs/remediation/test_deepagent_nodes.py` with a test that the node produces a plan + remediation per target. Mock the planning deepagent so no real LLM runs:

```python
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
            target_dep="lodash", addresses=["lodash"], current_range="^4.17.15", tier="r1"
        ).model_dump()
    }
    investigations = {
        "lodash": {
            "target_dep": "lodash",
            "dependents": [],
            "call_sites": [],
            "release": {
                "from_version": "4.17.15", "to_version": "4.17.21",
                "migration_needed": False, "migration_guide": "", "breaking_changes": [],
            },
        }
    }

    committed = {
        "lodash": {
            "target_dep": "lodash", "tier_hint": "r1", "migration_guide": "",
            "tasks": [{"kind": "bump", "rationale": "patch", "to_range": "^4.17.21",
                       "files": [], "replacement_dep": None, "replacement_range": None}],
            "requires": [],
        }
    }

    async def _fake_invoke(initial_state, run_config):
        return {"migration_plans": committed, "remediations": {}, "requires_edges": {}}

    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes._build_planning_agent",
        return_value=MagicMock(ainvoke=AsyncMock(side_effect=_fake_invoke)),
    ):
        out = await root_deepagent_node(
            {"job_id": "j", "prep_result_id": "p", "targets": targets,
             "investigations": investigations},
            config,
        )

    assert out["migration_plans"]["lodash"]["tier_hint"] == "r1"
    rem = out["remediations"]["lodash"]
    assert rem["target_dep"] == "lodash"
    assert rem["to_range"] == "^4.17.21"
    assert rem["plan"]["tasks"][0]["kind"] == "bump"
```

Keep `test_root_deepagent_node_no_targets_short_circuits` (adjust its expected return to include `"migration_plans": {}`).

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v -k "produces_plan or no_targets"`
Expected: FAIL — `_build_planning_agent` not defined and the node does not yet emit `migration_plans`/bump remediations.

- [ ] **Step 3: Reshape root_deepagent_node**

In `deepagent/nodes.py`, replace `_build_root_deep_agent`/`_root_deep_agent` and the body of `root_deepagent_node`. Build a per-invocation planning agent (its sandboxed subagents need `work_dir`, so it is built per node call, not module-level):

```python
from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_codemod_subagent,
    build_replacement_subagent,
)
from src.main_graph.subgraphs.remediation.deepagent.tools import make_commit_plan_tool
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.models.remediation import MigrationPlan

_PLANNER_PROMPT = """\
You plan and delegate dependency remediation for a Node.js project. For each
open target you are given: the tier hint, the release digest (whether a
migration is needed and a guide), the dependents, and the call sites.

For EACH target you MUST:
1. Call commit_plan with a MigrationPlan: a `bump` task for a clean upgrade;
   `bump` + `codemod` task(s) when the release digest says migration_needed;
   a `replace` task only when the tier hint is r3. Put companion deps in
   `requires`.
2. Then carry out the plan: dispatch codemod_adapter for codemod tasks
   (give it the migration guide and the files) and replacement_migrator for
   replace tasks. Do NOT edit code yourself. Bump tasks need no dispatch --
   they are applied deterministically after you finish.
Stop once every target has a committed plan and its non-bump tasks are
dispatched."""


def _build_planning_agent(work_dir, container, docker_image, package_manager):
    return create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        tools=[make_commit_plan_tool()],
        subagents=[
            build_codemod_subagent(work_dir, container, docker_image, package_manager),
            build_replacement_subagent(work_dir, container, docker_image, package_manager),
        ],
        system_prompt=_PLANNER_PROMPT,
        state_schema=RemediationDeepAgentState,
    )
```

Then rewrite `root_deepagent_node`. It resolves the working target set (retry vs. full, same as today), builds the evidence-seeded message, invokes the planning agent, and converts each committed `MigrationPlan` into a provisional `Remediation` (status left provisional; `group_and_verify_gate` sets the real status). Bump `to_range` comes from the plan's `bump` task; codemod/replace patches come from the subagents' `remediations` output.

```python
async def root_deepagent_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    targets = _resolve_working_targets(state, prep)  # helper: retry vs full (below)
    if not targets:
        return {"targets": {}, "remediations": {}, "requires_edges": {}, "migration_plans": {}}

    investigations = state.get("investigations") or {}
    open_list = _format_open_targets(targets, investigations)  # helper (below)

    work_dir = copy_repo(prep.repo_path)
    try:
        agent = _build_planning_agent(
            work_dir, container, prep.docker_image, prep.detected_package_manager
        )
        initial_state = {
            "messages": [{"role": "user", "content": open_list}],
            "job_id": state["job_id"],
            "prep_result_id": state["prep_result_id"],
            "targets": targets,
            "remediations": {},
            "requires_edges": {},
            "migration_plans": {},
        }
        run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
        try:
            result = await agent.ainvoke(initial_state, run_config)
        except GraphRecursionError:
            logger.warning(
                "root_deepagent_node: hit recursion_limit=%d; discarding round",
                _RECURSION_LIMIT,
            )
            return {"targets": targets, "remediations": {}, "requires_edges": {}, "migration_plans": {}}
    finally:
        shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)

    plans = result.get("migration_plans") or {}
    agent_remediations = result.get("remediations") or {}
    remediations = _remediations_from_plans(targets, plans, agent_remediations)
    return {
        "targets": targets,
        "remediations": remediations,
        "requires_edges": result.get("requires_edges") or {},
        "migration_plans": plans,
    }
```

Add the three helpers in the same file:

```python
def _resolve_working_targets(state: RemediationState, prep) -> dict[str, dict]:
    retry_targets = state.get("retry_targets")
    known = state.get("targets") or {}
    if not retry_targets:
        return known
    direct = prep.dependency_graph.get("direct") or {}
    out: dict[str, dict] = {}
    for dep in retry_targets:
        out[dep] = known.get(dep) or RemediationTarget(
            target_dep=dep, addresses=[], current_range=direct.get(dep)
        ).model_dump()
    return out


def _format_open_targets(targets: dict[str, dict], investigations: dict[str, dict]) -> str:
    lines = ["Open targets:"]
    for dep, t in targets.items():
        inv = investigations.get(dep) or {}
        rel = inv.get("release") or {}
        lines.append(
            f"- {dep} (tier={t.get('tier')}, addresses={t.get('addresses') or 'none'}, "
            f"migration_needed={rel.get('migration_needed')}, "
            f"call_sites={inv.get('call_sites') or []}, guide={rel.get('migration_guide') or ''})"
        )
    return "\n".join(lines)


def _remediations_from_plans(
    targets: dict[str, dict],
    plans: dict[str, dict],
    agent_remediations: dict[str, dict],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dep, target_dict in targets.items():
        target = RemediationTarget(**target_dict)
        plan_dict = plans.get(dep)
        if plan_dict is None:
            out[dep] = Remediation(
                addresses=target.addresses,
                target_dep=dep,
                from_range=target.current_range,
                status="failed",
                skip_reason="planner produced no MigrationPlan",
            ).model_dump()
            continue
        plan = MigrationPlan(**plan_dict)
        # A codemod/replace subagent already emitted a Remediation into
        # `remediations`; enrich it with the plan. Otherwise this is a
        # bump-only plan -- synthesize the bump Remediation deterministically.
        if dep in agent_remediations:
            rem = Remediation(**agent_remediations[dep])
            rem.plan = plan
        else:
            bump = next((t for t in plan.tasks if t.kind == "bump"), None)
            rem = Remediation(
                addresses=target.addresses,
                target_dep=dep,
                strategy="bump",
                from_range=target.current_range,
                to_range=bump.to_range if bump else None,
                status="skipped",  # provisional; gate sets real status
                plan=plan,
            )
        out[dep] = rem.model_dump()
    return out
```

Ensure `RemediationTarget`, `Remediation`, `shutil`, `os` remain imported (they already are in this module).

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "feat: reshape root node into plan_and_orchestrate with persisted plans"
```

---

### Task 9: Persist the plan through verify + PR

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/deepagent/nodes.py`
- Test: `tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Consumes: `group_and_verify_gate` and `pr_and_persist_node` (existing) now carry `Remediation.plan` through unchanged (it is a field on the dict they already round-trip via `Remediation(**...)`/`model_dump()`). This task adds one guard: `group_and_verify_gate`'s `replace`-strategy branch already settles `skipped` — confirm a `replace` plan produced by the stub lands there, and that `plan` survives `settled[...] = member_dict`.
- Produces: no new API — a regression test proving `plan` is present on a shipped remediation after the gate, and that a `replace` remediation settles skipped with its plan intact.

- [ ] **Step 1: Write the failing/confirming test**

Add to `tests/unit/subgraphs/remediation/test_deepagent_nodes.py` a test that runs `group_and_verify_gate` over a single `bump` remediation carrying a `plan`, with `replay_and_verify_group` patched green, and asserts `settled["lodash"]["plan"]` is preserved:

```python
@pytest.mark.asyncio
async def test_group_verify_preserves_plan_field():
    from src.models.remediation import MigrationPlan, Remediation, VerificationResult

    plan = MigrationPlan(target_dep="lodash", tier_hint="r1", tasks=[])
    rem = Remediation(
        addresses=["lodash"], target_dep="lodash", strategy="bump",
        to_range="^4.17.21", plan=plan,
    ).model_dump()

    prep = MagicMock(repo_path="/tmp/repo", docker_image="img", detected_package_manager="npm")
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    green = VerificationResult(installed=True, built=True, tested=True, finding_resolved=True)
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes.replay_and_verify_group",
        AsyncMock(return_value=green),
    ):
        out = await group_and_verify_gate(
            {"prep_result_id": "p", "remediations": {"lodash": rem},
             "requires_edges": {}, "targets": {"lodash": {}}},
            config,
        )
    assert out["remediations"]["lodash"]["status"] == "fixed"
    assert out["remediations"]["lodash"]["plan"]["target_dep"] == "lodash"
```

- [ ] **Step 2: Run to verify current behavior**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v -k preserves_plan`
Expected: PASS already if `plan` round-trips cleanly (it is a plain field on the dict). If it FAILS (e.g. `Remediation(**member_dict)` drops `plan` somewhere), fix by ensuring the gate never reconstructs a `Remediation` without re-dumping the full dict — it already mutates `member_dict` in place, so no code change should be needed. Only add code if the test fails.

- [ ] **Step 3: Commit**

```bash
git add apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "test: assert MigrationPlan survives verify/persist"
```

---

### Task 10: Wire investigate_node into the graph

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/graph.py`
- Test: `tests/subgraphs/test_remediation_subgraph.py`

**Interfaces:**
- Consumes: `investigate_node` (Task 5), reshaped `root_deepagent_node` (Task 8).
- Produces: the compiled subgraph with edges `START → classify_targets_node → investigate_node → root_deepagent_node → group_and_verify_gate → (route) → {root_deepagent_node | pr_and_persist_node} → END`.

- [ ] **Step 1: Update the graph builder**

In `graph.py`, add the import and node + edges:

```python
from src.main_graph.subgraphs.remediation.investigate import investigate_node
```

```python
    builder.add_node("investigate_node", investigate_node)
    ...
    builder.add_edge(START, "classify_targets_node")
    builder.add_edge("classify_targets_node", "investigate_node")
    builder.add_edge("investigate_node", "root_deepagent_node")
```

(Replace the current direct `classify_targets_node → root_deepagent_node` edge.)

- [ ] **Step 2: Update the subgraph integration test**

In `tests/subgraphs/test_remediation_subgraph.py`, ensure the end-to-end/wiring test seeds `investigations` or patches `investigate_node` so the run reaches `root_deepagent_node` with evidence. Run the existing suite first to see what breaks:

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_remediation_subgraph.py -v`
Expected: some failures where the test assumed the old 4-node path; update them to the 5-node path (add `investigate_node` to any asserted node sequence, and provide/patch investigation evidence).

- [ ] **Step 3: Fix the integration test to the new path**

Apply the minimal edits identified in Step 2 (add `investigate_node` to expected node lists; patch `_build_planning_agent` / `investigate_target` where the test previously patched the monolithic agent). Re-run until green.

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_remediation_subgraph.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/graph.py apps/backend/tests/subgraphs/test_remediation_subgraph.py
git commit -m "feat: wire investigate_node into the remediation subgraph"
```

---

### Task 11: Full verification

**Files:** None (verification only).

- [ ] **Step 1: Full backend test suite**

Run: `cd apps/backend && uv run pytest`
Expected: PASS, zero failures. Pay attention to any remaining reference to the removed monolithic `remediate_target` builder or the old `_root_deep_agent` module global.

- [ ] **Step 2: Lint + types**

Run: `cd apps/backend && uv run ruff check . && uv run mypy src`
Expected: both clean. Common fixes: remove the now-unused `Remediation` import from `classify.py`, unused `build_target_subagent` references, and the placeholder import line noted in Task 4 Step 3.

- [ ] **Step 3: Grep for dangling references to retired symbols**

Run: `cd apps/backend && rg -n "build_target_subagent|_root_deep_agent|remediate_target" src tests`
Expected: no hits in `src/`; any test hit should be updated or removed.

- [ ] **Step 4: Commit if fixes were needed**

```bash
git add -A
git commit -m "fix: resolve lint/type/test issues from planner decomposition"
```

Only commit if Steps 1-3 required changes.

---

## Self-Review Notes

- **Spec coverage:** D1 (tier hint, no r3 gate) → Task 6. D2 (guaranteed investigation; Dependency+Source deterministic, Release fetch+LLM digest) → Tasks 2-5. D3 (planning deepagent emits MigrationPlan via mandatory commit_plan before dispatch) → Tasks 7-8. D4 (typed agents: bump deterministic, codemod deepagent, replacement stub) → Tasks 7-8. D5 (MigrationPlan embedded on Remediation, persisted) → Tasks 1, 8, 9. D6 (uniform path, no r1 short-circuit) → Task 8 (`_remediations_from_plans` handles bump-only plans without special routing). D7 (`requires` from planning) → Task 8 (`requires_edges` from agent result). D8 (backstop untouched) → Tasks 9-10 leave gate/PR logic intact. D9 (honest bounds; malformed plan → failed) → Task 8 (`GraphRecursionError` handling + `_remediations_from_plans` None-plan → failed).
- **Placeholder scan:** the one intentional "remove this line" instruction (Task 4 Step 3 import) is called out explicitly with the corrected final import list; no `TBD`/`TODO`/"handle edge cases" left.
- **Type consistency:** `MigrationPlan`/`MigrationTask`/`ReleaseDigest`/`TargetInvestigation` names and fields are identical across Tasks 1, 3, 7, 8. `investigate_target`, `investigate_node`, `_build_planning_agent`, `make_commit_plan_tool`, `build_codemod_subagent`, `build_replacement_subagent` are referenced with the same signatures where defined and consumed. State channels `investigations`/`migration_plans` are added in Task 5 (outer) and Task 7 (deepagent) and read in Tasks 8/10.
- **Deferred by design:** replacement-migrator real behavior (Spec B); analysis-finding version-enrichment (`to_version` stays `None`, release fetch degrades to unfiltered recent — honest, per soft-dep); HITL plan gate; failure-log-informed repair.
