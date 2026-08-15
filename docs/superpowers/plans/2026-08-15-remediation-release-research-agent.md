# Remediation Release-Research Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `classify.py`'s single LLM call (tier + release digest) with a
deterministic `select_targets_node` (selection/version-resolution/r3-check,
no LLM) followed by an agentic `research_releases_node` that iterates over
paginated GitHub release notes and linked migration docs to produce a
richer digest for the migration planner.

**Architecture:** `select_targets_node` (new, deterministic) replaces
`classify_targets_node` as the remediation subgraph's first node --
selection/dedup, version+repo resolution, the r3 "no upgrade exists" check,
and blast-radius/dependents context, all ported from `classify.py` and the
deleted `selection.py` with no LLM. `research_releases_node` (new, agentic)
runs after it for every non-r3 target: a small structured-output loop
(shape borrowed from the analysis subgraph's `_react_loop`, not
`deepagents`) with two tools -- a paginated `get_release_notes` and an
SSRF-hardened `fetch_doc` for linked migration guides -- producing the same
`ReleaseDigest` shape `classify_target` used to write in one shot.
`classify.py` is deleted entirely.

**Tech Stack:** Python 3.11+, LangGraph, LangChain (`with_structured_output`,
`@tool`), Pydantic v2, `httpx` (direct calls, not containerized), `pytest`
+ `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-08-15-remediation-release-research-agent-design.md`

## Global Constraints

- No `--paginate` in any new `gh api` command -- always fetch one explicit
  page (`per_page=100&page=N`).
- `fetch_doc`'s SSRF check uses `ipaddress.ip_address(ip).is_global` (a
  single check covering RFC1918/loopback/link-local/metadata/reserved) --
  do not hand-roll an OR-chain of individual range checks.
- `GH_TOKEN` is attached only when the request host is exactly
  `github.com` or `raw.githubusercontent.com` (string equality, never
  substring/suffix match).
- Redirects in `fetch_doc` are never auto-followed (`follow_redirects=False`)
  -- each hop is manually re-validated, capped at 3 hops.
- Release/doc body text is capped at 2000 chars before reaching any LLM
  prompt (matches the existing convention in `changelog.py`).
- Concurrency across targets is bounded by a semaphore capped at 6 in both
  new nodes (matches `classify.py`'s existing `_MAX_CONCURRENT_CLASSIFICATIONS`
  convention and its still-passing concurrency test).
- Every new/moved function needs a docstring only where the WHY is
  non-obvious (existing repo convention) -- do not add narration comments.

---

### Task 1: Port `dependents_of` into `utils/dependency_graph.py`

**Files:**
- Modify: `apps/backend/src/utils/dependency_graph.py`
- Test: `apps/backend/tests/unit/test_dependency_graph_helpers.py` (already
  exists, already imports `dependents_of` -- currently fails at collection
  with `ImportError`; no test code changes needed in this task)

**Interfaces:**
- Produces: `dependents_of(graph: dict, name: str) -> list[str]` -- used by
  Task 4's `select_targets.py`.

- [ ] **Step 1: Confirm the test currently fails at collection**

Run: `cd apps/backend && uv run pytest tests/unit/test_dependency_graph_helpers.py -v`
Expected: FAIL/ERROR -- `ImportError: cannot import name 'dependents_of'`

- [ ] **Step 2: Add `dependents_of` to `utils/dependency_graph.py`**

Add this function after `direct_dependents` (currently ends around line 68):

```python
def dependents_of(graph: dict, name: str) -> list[str]:
    """Return every package name in the tree with a recorded dependency on
    any installed version of `name` -- not limited to direct-dependency
    roots, unlike direct_dependents(). Structural only: reflects the
    resolved graph, not whether a declared version range still holds after
    a bump -- that is what verification checks.
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

- [ ] **Step 3: Run the test suite to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_dependency_graph_helpers.py -v`
Expected: PASS -- all `test_dependents_of_*` and `test_direct_dependents_*` tests green

- [ ] **Step 4: Commit**

```bash
cd apps/backend && git add src/utils/dependency_graph.py
git commit -m "feat: add dependents_of to utils/dependency_graph"
```

---

### Task 2: Add `fetch_release_notes_page` to `changelog.py`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/changelog.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_changelog.py`

**Interfaces:**
- Consumes: `_resolve_github_repo`, `_tag_version`, `_tag_in_window` (all
  already in `changelog.py`, unchanged).
- Produces: `fetch_release_notes_page(package_name: str, page: int,
  from_version: str | None, to_version: str | None, repo_path: str,
  container: ContainerRunPort, docker_image: str, resolved_repo:
  tuple[str, str] | None = None) -> dict` -- returns
  `{"package_name", "available", "repository", "page", "has_more",
  "releases": [{"tag", "name", "body"}]}` on success, or
  `{"package_name", "available": False, "error"}` on failure. Used by
  Task 6's `get_release_notes` tool.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_changelog.py`:

```python
from src.main_graph.subgraphs.remediation.changelog import (
    fetch_release_notes_page,
)


@pytest.mark.asyncio
async def test_fetch_release_notes_page_windows_and_reports_no_more():
    releases_json = json.dumps(
        [
            {"tag_name": "v9.0.0", "name": "9.0.0", "body": "flat config"},
            {"tag_name": "v8.5.0", "name": "8.5.0", "body": "minor"},
        ]
    )
    container = FakeContainer([(0, releases_json, "")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            "8.0.0",
            "9.0.0",
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["available"] is True
    assert result["page"] == 1
    assert [r["tag"] for r in result["releases"]] == ["v9.0.0"]
    # Only 2 releases returned, well under per_page=100 -- no reason to
    # believe another page exists.
    assert result["has_more"] is False
    assert len(container.calls) == 1
    assert "per_page=100&page=1" in container.calls[0]["command"]
    assert "--paginate" not in container.calls[0]["command"]


@pytest.mark.asyncio
async def test_fetch_release_notes_page_has_more_when_page_full_and_above_floor():
    releases = [
        {"tag_name": f"v9.{i}.0", "name": f"9.{i}.0", "body": ""}
        for i in range(100, 0, -1)
    ]  # 100 releases, all above the 8.0.0 floor -- oldest is v9.1.0
    container = FakeContainer([(0, json.dumps(releases), "")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            "8.0.0",
            "9.100.0",
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_fetch_release_notes_page_no_more_when_page_reaches_floor():
    releases = [
        {"tag_name": "v8.1.0", "name": "8.1.0", "body": ""},
        {"tag_name": "v8.0.0", "name": "8.0.0", "body": ""},
    ]  # oldest tag (v8.0.0) is AT the floor, not above it
    container = FakeContainer([(0, json.dumps(releases), "")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            "8.0.0",
            "8.1.0",
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_fetch_release_notes_page_resolves_repo_when_not_given():
    container = FakeContainer(
        [
            (0, "git+https://github.com/eslint/eslint.git\n", ""),
            (0, "[]", ""),
        ]
    )
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint", 1, None, None, "/repo", container, "node:lts-alpine"
        )

    assert result["available"] is True
    assert len(container.calls) == 2


@pytest.mark.asyncio
async def test_fetch_release_notes_page_gh_failure_reports_unavailable():
    container = FakeContainer([(1, "", "HTTP 404: Not Found")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            None,
            None,
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["available"] is False
    assert "404" in result["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_changelog.py -k fetch_release_notes_page -v`
Expected: FAIL -- `ImportError: cannot import name 'fetch_release_notes_page'`

- [ ] **Step 3: Implement `fetch_release_notes_page`**

Add to `apps/backend/src/main_graph/subgraphs/remediation/changelog.py`,
after `fetch_release_notes_between`:

```python
async def fetch_release_notes_page(
    package_name: str,
    page: int,
    from_version: str | None,
    to_version: str | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
    resolved_repo: tuple[str, str] | None = None,
) -> dict:
    """Fetch ONE page (per_page=100) of a package's GitHub releases
    directly -- no --paginate, so this never fetches more than the caller
    asks for (unlike fetch_release_notes, which always fetches a package's
    entire history before slicing to 20 -- see the design spec). Windows to
    (from_version, to_version] the same way fetch_release_notes_between
    does. has_more is True only when there's reason to believe an older,
    still-relevant release exists: this page was full AND its oldest tag is
    still above the window floor.
    """
    resolved = resolved_repo or await _resolve_github_repo(
        package_name, repo_path, container, docker_image
    )
    if resolved is None:
        return {
            "package_name": package_name,
            "available": False,
            "error": "could not resolve a GitHub repository for this package",
        }
    owner, repo = resolved
    per_page = 100
    command = (
        "gh api "
        f"{shlex.quote(f'repos/{owner}/{repo}/releases?per_page={per_page}&page={page}')}"
    )
    secret_env = {"GH_TOKEN": settings.github_token} if settings.github_token else None
    rc, stdout, stderr = await container.run(
        image=settings.gh_docker_image,
        command=command,
        run_as_root=True,
        secret_env=secret_env,
    )
    if rc != 0:
        return {
            "package_name": package_name,
            "available": False,
            "error": stderr[:300],
        }
    try:
        releases = json.loads(stdout.strip() or "[]")
    except json.JSONDecodeError:
        return {
            "package_name": package_name,
            "available": False,
            "error": "unparseable gh output",
        }

    low = _tag_version(from_version)
    high = _tag_version(to_version)
    windowed = [
        {
            "tag": r.get("tag_name"),
            "name": r.get("name"),
            "body": (r.get("body") or "")[:2000],
        }
        for r in releases
        if low is None or high is None or _tag_in_window(r.get("tag"), low, high)
    ]

    oldest = _tag_version(releases[-1].get("tag_name")) if releases else None
    has_more = (
        len(releases) == per_page
        and low is not None
        and (oldest is None or oldest > low)
    )

    return {
        "package_name": package_name,
        "available": True,
        "repository": f"{owner}/{repo}",
        "page": page,
        "has_more": has_more,
        "releases": windowed,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_changelog.py -v`
Expected: PASS -- all tests in the file, including the new ones

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/remediation/changelog.py tests/unit/subgraphs/remediation/test_changelog.py
git commit -m "feat: add paginated fetch_release_notes_page to changelog.py"
```

---

### Task 3: Swap `AgentRole.REMEDIATION_CLASSIFY` for `REMEDIATION_RELEASE_RESEARCH`

**Files:**
- Modify: `apps/backend/src/utils/model_registry.py`

**Interfaces:**
- Produces: `AgentRole.REMEDIATION_RELEASE_RESEARCH` -- used by Task 7's
  `release_research.py`.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/utils/test_model_registry.py`:

```python
def test_remediation_release_research_role_exists_and_classify_role_removed():
    assert AgentRole.REMEDIATION_RELEASE_RESEARCH.value == "remediation_release_research"
    assert not hasattr(AgentRole, "REMEDIATION_CLASSIFY")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/utils/test_model_registry.py -k remediation_release_research -v`
Expected: FAIL -- `AttributeError: REMEDIATION_RELEASE_RESEARCH`

- [ ] **Step 3: Edit the `AgentRole` enum**

In `apps/backend/src/utils/model_registry.py`, replace:

```python
    REMEDIATION_CLASSIFY = "remediation_classify"
```

with:

```python
    REMEDIATION_RELEASE_RESEARCH = "remediation_release_research"
```

(leave `REMEDIATION_PLAN` and `REMEDIATION_EXECUTION_DEEPAGENT` untouched)

- [ ] **Step 4: Run the full model_registry test file**

Run: `cd apps/backend && uv run pytest tests/unit/utils/test_model_registry.py -v`
Expected: PASS -- including `test_resolve_model_defaults_to_gpt_5_4_mini_for_every_role`,
which iterates `AgentRole` and picks up the new member automatically

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/utils/model_registry.py tests/unit/utils/test_model_registry.py
git commit -m "feat: replace AgentRole.REMEDIATION_CLASSIFY with REMEDIATION_RELEASE_RESEARCH"
```

---

### Task 4: Create `select_targets.py`, delete `classify.py`

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/remediation/select_targets.py`
- Create: `apps/backend/tests/unit/subgraphs/remediation/test_select_targets.py`
- Delete: `apps/backend/src/main_graph/subgraphs/remediation/classify.py`
- Delete: `apps/backend/tests/unit/subgraphs/remediation/test_classify.py`

**Interfaces:**
- Consumes: `resolve_package_info` (`changelog.py`, unchanged),
  `compute_blast_radius` (`src/main_graph/tools/blast_radius.py:12`,
  unchanged, signature `(package_name: str, repo_path: str, container:
  ContainerRunPort) -> dict` with keys `available`/`affected_files`),
  `dependents_of`/`direct_dependents`/`is_direct` (Task 1 +
  `utils/dependency_graph.py`, unchanged), `settings.risk_min_severity`,
  `settings.codegraph_docker_image`.
- Produces: `select_remediation_targets(findings: list[FindingNote],
  dependency_graph: dict, min_severity: str) -> list[RemediationTarget]`,
  `select_targets_node(state: RemediationState, config: RunnableConfig) ->
  dict` (returns `{"targets": dict[str, dict], "investigations": dict[str,
  dict], "remediations": {}}`) -- `select_targets_node` is wired into
  `graph.py` in Task 9; `research_releases_node` (Task 7) reads `targets`
  and `investigations` from state.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/subgraphs/remediation/test_select_targets.py`:

```python
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.select_targets import (
    _has_no_upgrade,
    select_remediation_targets,
    select_targets_node,
)
from src.models.conductor import FindingNote
from src.models.remediation import RemediationTarget
from src.models.results import PrepResult

_DEP_GRAPH = {"direct": {"lodash": "^4.17.11"}, "packages": {}}


def _no_blast_radius():
    return patch(
        "src.main_graph.subgraphs.remediation.select_targets.compute_blast_radius",
        AsyncMock(return_value={"available": False}),
    )


def _no_index(**overrides):
    return patch(
        "src.main_graph.subgraphs.remediation.select_targets._index_codegraph",
        AsyncMock(return_value=overrides.get("return_value", True)),
    )


def _prep(**overrides):
    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        docker_image="node:lts-alpine",
        dependency_graph=_DEP_GRAPH,
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


def _finding(dep_name: str, severity: str = "high") -> FindingNote:
    return FindingNote(dep_name=dep_name, severity=severity, description="d", evidence=[])


# --- select_remediation_targets (pure, deterministic) ----------------------


def test_select_remediation_targets_anchors_transitive_to_direct_parent():
    graph = {
        "direct": {"webpack": "5.0.0"},
        "packages": {"webpack@5.0.0": {"dependencies": ["qs@6.5.2"]}, "qs@6.5.2": {}},
    }
    targets = select_remediation_targets([_finding("qs")], graph, "low")
    assert len(targets) == 1
    assert targets[0].target_dep == "webpack"
    assert targets[0].addresses == ["qs"]


def test_select_remediation_targets_drops_finding_with_no_anchor():
    graph = {"direct": {}, "packages": {}}
    assert select_remediation_targets([_finding("orphan")], graph, "low") == []


def test_select_remediation_targets_filters_by_severity():
    targets = select_remediation_targets(
        [_finding("lodash", severity="low")], _DEP_GRAPH, "high"
    )
    assert targets == []


# --- _has_no_upgrade (pure) --------------------------------------------------


def test_has_no_upgrade_true_when_latest_at_or_below_floor():
    assert _has_no_upgrade("^4.17.11", "4.17.11") is True


def test_has_no_upgrade_false_when_upgrade_exists():
    assert _has_no_upgrade("^4.17.11", "4.17.21") is False


def test_has_no_upgrade_false_when_either_side_missing():
    assert _has_no_upgrade(None, "4.17.21") is False
    assert _has_no_upgrade("^4.17.11", None) is False


# --- select_targets_node -----------------------------------------------------


@pytest.mark.asyncio
async def test_select_targets_node_forces_r3_when_no_upgrade_exists():
    prep = _prep(dependency_graph={"direct": {"matcha": "0.7.0"}, "packages": {}})
    analysis = MagicMock(findings=[_finding("matcha")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=("0.7.0", None)),
        ),
        _no_blast_radius(),
        _no_index(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert result["targets"]["matcha"]["tier"] == "r3"
    assert result["remediations"] == {}


@pytest.mark.asyncio
async def test_select_targets_node_leaves_tier_unset_when_upgrade_exists():
    prep = _prep()
    analysis = MagicMock(findings=[_finding("lodash")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=("4.17.21", ("lodash", "lodash"))),
        ),
        _no_blast_radius(),
        _no_index(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert result["targets"]["lodash"]["tier"] is None
    assert result["investigations"]["lodash"]["release"]["migration_needed"] is False


@pytest.mark.asyncio
async def test_select_targets_node_survives_codegraph_index_failure():
    """A failed/unavailable codegraph init must not crash the whole node --
    targets/investigations must still populate, unlike the old classify.py
    bug this replaces."""
    prep = _prep()
    analysis = MagicMock(findings=[_finding("lodash")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    container = AsyncMock()
    container.run.side_effect = RuntimeError("docker daemon unreachable")
    config = {"configurable": {"result_dao": dao, "container": container}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=("4.17.21", ("lodash", "lodash"))),
        ),
        _no_blast_radius(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert result["targets"]["lodash"]["target_dep"] == "lodash"
    assert result["investigations"]["lodash"]["target_dep"] == "lodash"


@pytest.mark.asyncio
async def test_select_targets_node_populates_dependents_and_call_sites():
    graph = {
        "direct": {"webpack": "5.0.0"},
        "packages": {
            "webpack@5.0.0": {"dependencies": ["qs@6.5.2"]},
            "qs@6.5.2": {},
        },
    }
    prep = _prep(dependency_graph=graph)
    analysis = MagicMock(findings=[_finding("qs")])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(return_value=(None, None)),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.compute_blast_radius",
            AsyncMock(
                return_value={"available": True, "affected_files": ["src/a.ts:3"]}
            ),
        ),
        _no_index(),
    ):
        result = await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    inv = result["investigations"]["webpack"]
    assert inv["call_sites"] == ["src/a.ts:3"]
    # webpack is itself a direct dep -- dependents_of("webpack") on this
    # 2-package graph is [] (nothing depends on webpack here); the point of
    # this test is that the field is wired, not any specific graph shape.
    assert inv["dependents"] == []


@pytest.mark.asyncio
async def test_select_targets_node_bounds_concurrency():
    n_targets = 20
    deps = [f"dep-{i}" for i in range(n_targets)]
    prep = _prep(dependency_graph={"direct": {d: "1.0.0" for d in deps}, "packages": {}})
    analysis = MagicMock(findings=[_finding(d) for d in deps])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    current = 0
    peak = 0
    lock = asyncio.Lock()

    async def _fake_resolve(*args, **kwargs):
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1
        return (None, None)

    with (
        patch(
            "src.main_graph.subgraphs.remediation.select_targets.resolve_package_info",
            AsyncMock(side_effect=_fake_resolve),
        ),
        _no_blast_radius(),
        _no_index(),
    ):
        await select_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert peak <= 6, f"expected concurrency to be capped at 6, observed {peak}"
    assert peak > 1


@pytest.mark.asyncio
async def test_select_targets_node_no_findings_short_circuits():
    prep = _prep()
    analysis = MagicMock(findings=[])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    result = await select_targets_node(
        {
            "job_id": "job-1",
            "prep_result_id": "prep-1",
            "analysis_result_id": "a-1",
            "concern": "c",
        },
        config,
    )
    assert result == {"targets": {}, "investigations": {}, "remediations": {}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_select_targets.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named
'src.main_graph.subgraphs.remediation.select_targets'`

- [ ] **Step 3: Create `select_targets.py`**

Create `apps/backend/src/main_graph/subgraphs/remediation/select_targets.py`:

```python
"""Deterministic target selection for remediation: turns analysis findings
into RemediationTargets (dedup, direct-dep anchoring), resolves each
target's registry version + GitHub repo, decides the deterministic r3
"no upgrade exists" tier, and gathers blast-radius/dependents context for
the migration planner. No LLM -- see docs/superpowers/specs/2026-08-15-
remediation-release-research-agent-design.md (D-SELECT). Replaces the
former classify.py, which combined this with an LLM tier+digest call now
done by release_research.py instead."""

from __future__ import annotations

import asyncio
import logging

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.changelog import resolve_package_info
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.tools.blast_radius import compute_blast_radius
from src.models.conductor import FindingNote
from src.models.remediation import (
    FindingSummary,
    ReleaseDigest,
    RemediationTarget,
    TargetInvestigation,
)
from src.utils.config import settings
from src.utils.dependency_graph import dependents_of, direct_dependents, is_direct
from src.utils.semver import parse_semver, range_floor
from src.utils.severity import SEVERITY_ORDER, filter_by_min_severity

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_SELECTION = 6
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SELECTION)


def _anchors(graph: dict, dep_name: str) -> list[str]:
    if is_direct(graph, dep_name):
        return [dep_name]
    return direct_dependents(graph, dep_name)


def select_remediation_targets(
    findings: list[FindingNote], dependency_graph: dict, min_severity: str
) -> list[RemediationTarget]:
    """Deterministic: filter by severity, anchor transitives to their direct
    dependent(s), unify findings that share a direct-dep bump.

    Findings with no direct anchor (no lever the user controls) are
    dropped. A dep with multiple findings (e.g. vuln + maintenance) keeps
    the highest-severity one for its FindingSummary -- ties keep whichever
    was seen first."""
    survivors = filter_by_min_severity(findings, min_severity)
    direct = dependency_graph.get("direct") or {}

    grouped: dict[str, set[str]] = {}
    summaries: dict[str, dict[str, FindingSummary]] = {}
    for finding in survivors:
        for anchor in _anchors(dependency_graph, finding.dep_name):
            grouped.setdefault(anchor, set()).add(finding.dep_name)
            anchor_summaries = summaries.setdefault(anchor, {})
            existing = anchor_summaries.get(finding.dep_name)
            if existing is None or SEVERITY_ORDER.get(
                finding.severity, 0
            ) > SEVERITY_ORDER.get(existing.severity, 0):
                anchor_summaries[finding.dep_name] = FindingSummary(
                    dep_name=finding.dep_name,
                    severity=finding.severity,
                    description=finding.description,
                )

    return [
        RemediationTarget(
            target_dep=dep,
            addresses=sorted(addressed),
            finding_summaries=[summaries[dep][name] for name in sorted(addressed)],
            current_range=direct.get(dep),
        )
        for dep, addressed in sorted(grouped.items())
    ]


def _has_no_upgrade(current_range: str | None, latest_version: str | None) -> bool:
    """True when the registry's newest published version is not above the
    floor of the range already declared -- i.e. no same-package upgrade
    exists at all. Compares against the range's FLOOR rather than whatever
    the lockfile resolved, so this only fires when nothing higher than even
    the lowest accepted version was ever published -- conservative: it can
    force r3, never block it."""
    if not current_range or not latest_version:
        return False
    floor = range_floor(current_range)
    latest = parse_semver(latest_version)
    if floor is None or latest is None:
        return False
    return latest <= floor


async def _resolve_bounded(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> tuple[str | None, tuple[str, str] | None]:
    async with _semaphore:
        return await resolve_package_info(
            target.target_dep, repo_path, container, docker_image
        )


async def _enrich_bounded(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    dependency_graph: dict,
) -> TargetInvestigation:
    async with _semaphore:
        try:
            blast = await compute_blast_radius(target.target_dep, repo_path, container)
            call_sites = blast.get("affected_files", []) if blast.get("available") else []
        except Exception as exc:
            logger.warning(
                "_enrich_bounded: blast radius failed for %s: %s", target.target_dep, exc
            )
            call_sites = []
        return TargetInvestigation(
            target_dep=target.target_dep,
            dependents=dependents_of(dependency_graph, target.target_dep),
            call_sites=call_sites,
            release=ReleaseDigest(
                from_version=target.current_range,
                to_version=target.latest_version,
                migration_needed=False,
            ),
        )


async def _index_codegraph(repo_path: str, container: ContainerRunPort) -> bool:
    """Build the CodeGraph blast-radius index for repo_path."""
    try:
        rc, _out, err = await container.run(
            image=settings.codegraph_docker_image,
            command="codegraph init --force /workspace",
            volume=f"{repo_path}:/workspace",
            run_as_root=True,
        )
        if rc != 0:
            logger.warning("_index_codegraph: init failed rc=%d err=%s", rc, err[:300])
            return False
    except Exception as exc:
        logger.warning("_index_codegraph: init failed: %s", exc)
        return False
    return True


async def select_targets_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])
    analysis = await dao.get_analysis(state["analysis_result_id"])

    initial = select_remediation_targets(
        analysis.findings, prep.dependency_graph, settings.risk_min_severity
    )
    if not initial:
        return {"targets": {}, "investigations": {}, "remediations": {}}

    resolved = await asyncio.gather(
        *[
            _resolve_bounded(t, prep.repo_path, container, prep.docker_image)
            for t in initial
        ]
    )
    for target, (latest_version, resolved_repo) in zip(initial, resolved, strict=True):
        target.latest_version = latest_version
        target.resolved_repo = resolved_repo
        if _has_no_upgrade(target.current_range, target.latest_version):
            target.tier = "r3"

    # Index once for the whole repo. Blast radius below degrades to empty
    # call_sites on failure (never crashes), so targets/investigations are
    # always populated regardless of whether this succeeds -- unlike the
    # classify.py bug this replaces, which left both unbound entirely.
    await _index_codegraph(prep.repo_path, container)

    investigations = await asyncio.gather(
        *[
            _enrich_bounded(t, prep.repo_path, container, prep.dependency_graph)
            for t in initial
        ]
    )

    targets: dict[str, dict] = {}
    investigations_out: dict[str, dict] = {}
    for target, investigation in zip(initial, investigations, strict=True):
        targets[target.target_dep] = target.model_dump()
        investigations_out[target.target_dep] = investigation.model_dump()

    return {"targets": targets, "investigations": investigations_out, "remediations": {}}
```

- [ ] **Step 4: Delete `classify.py` and `test_classify.py`**

```bash
cd apps/backend
git rm src/main_graph/subgraphs/remediation/classify.py
git rm tests/unit/subgraphs/remediation/test_classify.py
```

- [ ] **Step 5: Run the new test file to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_select_targets.py -v`
Expected: PASS -- all tests green

- [ ] **Step 6: Search for any remaining reference to `classify.py`**

Run: `cd apps/backend && grep -rn "subgraphs.remediation.classify\|from .classify\|import classify" src/ tests/ --include='*.py'`
Expected: no output except `graph.py` (updated in Task 9) and
`test_remediation_subgraph.py` (updated in Task 10) -- if anything else
shows up, note it for Task 9/10 to also touch, or fix here if it's
unrelated to those tasks

- [ ] **Step 7: Commit**

```bash
cd apps/backend && git add -A src/main_graph/subgraphs/remediation/select_targets.py \
  tests/unit/subgraphs/remediation/test_select_targets.py \
  src/main_graph/subgraphs/remediation/classify.py \
  tests/unit/subgraphs/remediation/test_classify.py
git commit -m "feat: replace classify.py with deterministic select_targets_node"
```

---

### Task 5: `release_research.py` — SSRF-hardened `fetch_doc`

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/remediation/release_research.py`
- Create: `apps/backend/tests/unit/subgraphs/remediation/test_release_research.py`

**Interfaces:**
- Consumes: `httpx` (already a dependency), `settings.github_token`.
- Produces: `fetch_doc(url: str) -> dict` (plain async function, not yet
  wrapped as a `@tool` -- that happens in this same task) returning
  `{"available": True, "url": str, "body": str}` or `{"available": False,
  "error": str}`. Used directly by this task's tests; wrapped by
  `make_fetch_doc_tool()` for Task 7's loop.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/subgraphs/remediation/test_release_research.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.main_graph.subgraphs.remediation.release_research import (
    fetch_doc,
    make_fetch_doc_tool,
)


def _resp(status_code: int, text: str = "", location: str | None = None):
    headers = {"location": location} if location else {}
    return httpx.Response(status_code, text=text, headers=headers)


@pytest.mark.asyncio
async def test_fetch_doc_rejects_non_http_scheme():
    result = await fetch_doc("file:///etc/passwd")
    assert result["available"] is False
    assert "scheme" in result["error"]


@pytest.mark.asyncio
async def test_fetch_doc_rejects_private_ip_host():
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.5", 0))]):
        result = await fetch_doc("http://internal.example.com/MIGRATION.md")
    assert result["available"] is False
    assert "public" in result["error"]


@pytest.mark.asyncio
async def test_fetch_doc_rejects_metadata_ip_host():
    with patch(
        "socket.getaddrinfo",
        return_value=[(None, None, None, None, ("169.254.169.254", 0))],
    ):
        result = await fetch_doc("http://metadata.internal/latest/meta-data/")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_doc_rejects_unresolvable_host():
    with patch("socket.getaddrinfo", side_effect=OSError("name resolution failed")):
        result = await fetch_doc("http://does-not-exist.invalid/doc.md")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_doc_success_returns_capped_body():
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("140.82.121.3", 0))],
        ),
        patch(
            "httpx.AsyncClient.get",
            AsyncMock(return_value=_resp(200, text="x" * 5000)),
        ),
    ):
        result = await fetch_doc("https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md")
    assert result["available"] is True
    assert len(result["body"]) == 2000


@pytest.mark.asyncio
async def test_fetch_doc_attaches_gh_token_only_for_github_hosts():
    captured = {}

    async def _fake_get(self, url, headers=None):
        captured["headers"] = headers
        return _resp(200, text="ok")

    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("140.82.121.3", 0))],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
        patch(
            "src.main_graph.subgraphs.remediation.release_research.settings"
        ) as mock_settings,
    ):
        mock_settings.github_token = "ghp_test"
        await fetch_doc("https://github.com/eslint/eslint/blob/main/MIGRATION.md")
    assert captured["headers"].get("Authorization") == "Bearer ghp_test"

    captured.clear()
    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
        patch(
            "src.main_graph.subgraphs.remediation.release_research.settings"
        ) as mock_settings,
    ):
        mock_settings.github_token = "ghp_test"
        await fetch_doc("https://example.com/docs/upgrade.md")
    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_fetch_doc_validates_redirect_target_before_following():
    """A redirect to a private IP must be rejected, not silently followed --
    the whole point of disabling auto-follow-redirects."""
    calls = {"n": 0}

    async def _fake_get(self, url, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(302, location="http://169.254.169.254/latest/meta-data/")
        raise AssertionError("must not follow the redirect to a private IP")

    with (
        patch(
            "socket.getaddrinfo",
            side_effect=[
                [(None, None, None, None, ("93.184.216.34", 0))],  # initial host: public
                [(None, None, None, None, ("169.254.169.254", 0))],  # redirect target
            ],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
    ):
        result = await fetch_doc("https://example.com/redirect-to-metadata")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_doc_gives_up_after_max_redirects():
    async def _fake_get(self, url, headers=None):
        return _resp(302, location="https://example.com/next")

    with (
        patch(
            "socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 0))],
        ),
        patch("httpx.AsyncClient.get", _fake_get),
    ):
        result = await fetch_doc("https://example.com/loop")
    assert result["available"] is False
    assert "redirect" in result["error"]


@pytest.mark.asyncio
async def test_make_fetch_doc_tool_delegates_to_fetch_doc():
    tool = make_fetch_doc_tool()
    with patch(
        "src.main_graph.subgraphs.remediation.release_research.fetch_doc",
        AsyncMock(return_value={"available": True, "url": "u", "body": "b"}),
    ) as mock_fetch:
        result = await tool.ainvoke({"url": "https://example.com/doc.md"})
    mock_fetch.assert_awaited_once_with("https://example.com/doc.md")
    assert result["available"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_release_research.py -v`
Expected: FAIL -- `ModuleNotFoundError`

- [ ] **Step 3: Create `release_research.py` with `fetch_doc` and its tool wrapper**

Create `apps/backend/src/main_graph/subgraphs/remediation/release_research.py`:

```python
"""Agentic release-note research for remediation: for every non-r3 target
select_targets_node produces, iterates paginated GitHub release notes and
any linked migration docs to produce the ReleaseDigest the migration
planner reads. See docs/superpowers/specs/2026-08-15-remediation-release-
research-agent-design.md (D-RESEARCH, D-TOOLS)."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from src.utils.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_GH_TOKEN_HOSTS = {"github.com", "raw.githubusercontent.com"}
_MAX_REDIRECTS = 3
_TIMEOUT = 10.0
_DOC_CHAR_CAP = 2000


def _resolve_public_ips(host: str) -> bool:
    """True only if `host` resolves and EVERY resolved address is globally
    routable. is_global covers RFC1918/loopback/link-local (including the
    169.254.169.254 cloud metadata address)/reserved/multicast in one
    check -- deliberately not a hand-rolled OR of individual range checks.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    ips = {info[4][0] for info in infos}
    if not ips:
        return False
    for ip in ips:
        try:
            if not ipaddress.ip_address(ip).is_global:
                return False
        except ValueError:
            return False
    return True


async def _fetch_doc_once(url: str) -> dict:
    """One hop: validate the URL, GET without following redirects. Returns
    either the terminal {"available": ...} result, or an internal
    {"_redirect": location} for the caller to re-validate and retry."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return {"available": False, "error": f"unsupported scheme: {parsed.scheme!r}"}
    if not parsed.hostname:
        return {"available": False, "error": "no host in URL"}
    if not _resolve_public_ips(parsed.hostname):
        return {
            "available": False,
            "error": "URL host does not resolve to a public address",
        }

    headers = {}
    if parsed.hostname in _GH_TOKEN_HOSTS and settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        r = await client.get(url, headers=headers)

    if 300 <= r.status_code < 400 and r.headers.get("location"):
        return {"_redirect": r.headers["location"]}
    if r.status_code >= 400:
        return {"available": False, "error": f"HTTP {r.status_code}"}
    return {"available": True, "url": url, "body": r.text[:_DOC_CHAR_CAP]}


async def fetch_doc(url: str) -> dict:
    """Fetch a URL a release body links to (MIGRATION.md, UPGRADING.md, an
    external guide). Hardened against SSRF: rejects non-http(s) schemes and
    any host that doesn't resolve to a public IP; GH_TOKEN is only attached
    when the validated host is exactly github.com or
    raw.githubusercontent.com. Redirects are never auto-followed -- each
    hop's target is re-validated the same way, up to 3 hops, so a redirect
    can't be used to reach a host the initial check would have rejected."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            result = await _fetch_doc_once(current)
        except Exception as exc:
            return {"available": False, "error": str(exc)}
        redirect = result.get("_redirect")
        if redirect is None:
            return result
        current = redirect
    return {"available": False, "error": "too many redirects"}


def make_fetch_doc_tool():
    @tool
    async def fetch_doc_tool(url: str) -> dict:
        """Fetch a document a release body links to (e.g. MIGRATION.md,
        UPGRADING.md, an external upgrade guide) when the release body
        itself just points at it instead of describing the change. Only
        public http(s) URLs are reachable."""
        return await fetch_doc(url)

    fetch_doc_tool.name = "fetch_doc"
    return fetch_doc_tool
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_release_research.py -v`
Expected: PASS -- all `fetch_doc`/`make_fetch_doc_tool` tests green

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/remediation/release_research.py \
  tests/unit/subgraphs/remediation/test_release_research.py
git commit -m "feat: add SSRF-hardened fetch_doc tool to release_research.py"
```

---

### Task 6: `release_research.py` — paginated `get_release_notes` tool

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/release_research.py`
- Modify: `apps/backend/tests/unit/subgraphs/remediation/test_release_research.py`

**Interfaces:**
- Consumes: `fetch_release_notes_page` (Task 2, `changelog.py`).
- Produces: `make_get_release_notes_tool(target_dep: str, from_version:
  str | None, to_version: str | None, resolved_repo: tuple[str, str] |
  None, repo_path: str, container: ContainerRunPort, docker_image: str)` --
  returns a `@tool` callable `get_release_notes(page: int = 1) -> dict`.
  Used by Task 7's `_research_loop`.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_release_research.py`:

```python
from src.main_graph.subgraphs.remediation.release_research import (
    make_get_release_notes_tool,
)


@pytest.mark.asyncio
async def test_get_release_notes_tool_delegates_with_closed_over_args():
    container = MagicMock()
    with patch(
        "src.main_graph.subgraphs.remediation.release_research.fetch_release_notes_page",
        AsyncMock(return_value={"available": True, "page": 1, "has_more": False, "releases": []}),
    ) as mock_fetch:
        tool = make_get_release_notes_tool(
            "eslint", "7.0.0", "8.0.0", ("eslint", "eslint"), "/repo", container, "node:lts-alpine"
        )
        result = await tool.ainvoke({"page": 1})

    mock_fetch.assert_awaited_once_with(
        "eslint", 1, "7.0.0", "8.0.0", "/repo", container, "node:lts-alpine",
        resolved_repo=("eslint", "eslint"),
    )
    assert result["available"] is True


@pytest.mark.asyncio
async def test_get_release_notes_tool_refuses_page_beyond_ten():
    tool = make_get_release_notes_tool(
        "eslint", "7.0.0", "8.0.0", None, "/repo", MagicMock(), "node:lts-alpine"
    )
    result = await tool.ainvoke({"page": 11})
    assert result["available"] is False
    assert "page limit" in result["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_release_research.py -k get_release_notes -v`
Expected: FAIL -- `ImportError: cannot import name 'make_get_release_notes_tool'`

- [ ] **Step 3: Add `make_get_release_notes_tool` to `release_research.py`**

Add near the top imports:

```python
from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes_page
```

Add after `make_fetch_doc_tool`:

```python
def make_get_release_notes_tool(
    target_dep: str,
    from_version: str | None,
    to_version: str | None,
    resolved_repo: tuple[str, str] | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
):
    @tool
    async def get_release_notes(page: int = 1) -> dict:
        """Fetch one page of the target package's GitHub releases, windowed
        to the versions between the installed range and the target
        version. Returns has_more=True when an older, still-relevant
        release likely exists on the next page -- call again with the next
        page number if you need more evidence. Refuses page > 10."""
        if page > 10:
            return {"available": False, "error": "page limit (10) exceeded"}
        return await fetch_release_notes_page(
            target_dep,
            page,
            from_version,
            to_version,
            repo_path,
            container,
            docker_image,
            resolved_repo=resolved_repo,
        )

    return get_release_notes
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_release_research.py -v`
Expected: PASS -- all tests in the file

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/remediation/release_research.py \
  tests/unit/subgraphs/remediation/test_release_research.py
git commit -m "feat: add paginated get_release_notes tool to release_research.py"
```

---

### Task 7: `release_research.py` — the research loop and `research_releases_node`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/release_research.py`
- Modify: `apps/backend/tests/unit/subgraphs/remediation/test_release_research.py`

**Interfaces:**
- Consumes: `AgentRole.REMEDIATION_RELEASE_RESEARCH`/`get_role_llm` (Task
  3), `make_get_release_notes_tool`/`make_fetch_doc_tool` (Tasks 5-6),
  `ToolCall`/`ToolResult` (`src/models/conductor.py`, unchanged),
  `ReleaseDigest`/`RemediationTarget`/`TargetInvestigation`
  (`src/models/remediation.py`, unchanged), `RemediationState`
  (`state.py`, unchanged -- `investigations` already uses the
  `_merge_replace` reducer).
- Produces: `research_releases_node(state: RemediationState, config:
  RunnableConfig) -> dict` (returns `{"investigations": dict[str, dict]}`,
  only the subset it updated) -- wired into `graph.py` in Task 9.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_release_research.py`:

```python
from src.main_graph.subgraphs.remediation.release_research import (
    ReleaseResearchDecision,
    research_releases_node,
)
from src.models.conductor import ToolCall
from src.models.remediation import ReleaseDigest


def _decision(**overrides) -> ReleaseResearchDecision:
    defaults = dict(
        tool_calls=[],
        finalize=True,
        migration_needed=False,
        migration_guide="",
        breaking_changes=[],
        reasoning="done",
    )
    defaults.update(overrides)
    return ReleaseResearchDecision(**defaults)


def _prep(**overrides):
    from src.models.results import PrepResult

    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        docker_image="node:lts-alpine",
        dependency_graph={"direct": {}, "packages": {}},
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


@pytest.mark.asyncio
async def test_research_releases_node_finalizes_immediately_when_told():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_decision(
            migration_needed=True,
            migration_guide="switch to flat config",
            breaking_changes=["flat config replaces .eslintrc"],
        )
    )

    with patch(
        "src.main_graph.subgraphs.remediation.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    inv = result["investigations"]["eslint"]
    assert inv["release"]["migration_needed"] is True
    assert inv["release"]["migration_guide"] == "switch to flat config"
    mock_llm.with_structured_output.assert_called_once()


@pytest.mark.asyncio
async def test_research_releases_node_skips_r3_targets():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "matcha",
        "addresses": ["matcha"],
        "current_range": "0.7.0",
        "latest_version": "0.7.0",
        "resolved_repo": None,
        "tier": "r3",
    }
    mock_llm = MagicMock()

    with patch(
        "src.main_graph.subgraphs.remediation.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"matcha": target},
                "investigations": {},
            },
            config,
        )

    assert result["investigations"] == {}
    mock_llm.with_structured_output.assert_not_called()


@pytest.mark.asyncio
async def test_research_releases_node_iterates_tool_calls_before_finalizing():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    responses = [
        _decision(
            finalize=False,
            tool_calls=[ToolCall(tool="get_release_notes", args={"page": 1}, reason="check notes")],
        ),
        _decision(migration_needed=False),
    ]
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=responses)

    with (
        patch(
            "src.main_graph.subgraphs.remediation.release_research._llm", mock_llm
        ),
        patch(
            "src.main_graph.subgraphs.remediation.release_research.fetch_release_notes_page",
            AsyncMock(return_value={"available": True, "page": 1, "has_more": False, "releases": []}),
        ),
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 2
    assert result["investigations"]["eslint"]["release"]["migration_needed"] is False


@pytest.mark.asyncio
async def test_research_releases_node_sources_guide_from_linked_doc():
    """The concrete scenario this whole node exists for: a release body
    just points at MIGRATION.md instead of describing the change -- the
    agent must be able to fetch_doc it and ground migration_guide in what
    that doc actually says, not the release body's pointer text."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    responses = [
        # 1: reads release notes, sees a pointer to MIGRATION.md
        _decision(
            finalize=False,
            tool_calls=[
                ToolCall(tool="get_release_notes", args={"page": 1}, reason="check notes")
            ],
        ),
        # 2: notes just said "see MIGRATION.md" -- fetches it
        _decision(
            finalize=False,
            tool_calls=[
                ToolCall(
                    tool="fetch_doc",
                    args={"url": "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md"},
                    reason="release body points here",
                )
            ],
        ),
        # 3: grounds the guide in the doc's actual content
        _decision(
            migration_needed=True,
            migration_guide="Replace .eslintrc with eslint.config.js (from MIGRATION.md)",
            breaking_changes=["flat config replaces .eslintrc"],
        ),
    ]
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=responses)

    with (
        patch(
            "src.main_graph.subgraphs.remediation.release_research._llm", mock_llm
        ),
        patch(
            "src.main_graph.subgraphs.remediation.release_research.fetch_release_notes_page",
            AsyncMock(
                return_value={
                    "available": True,
                    "page": 1,
                    "has_more": False,
                    "releases": [
                        {"tag": "v8.0.0", "name": "8.0.0", "body": "see MIGRATION.md"}
                    ],
                }
            ),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.release_research.fetch_doc",
            AsyncMock(
                return_value={
                    "available": True,
                    "url": "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md",
                    "body": "Replace .eslintrc with eslint.config.js.",
                }
            ),
        ) as mock_fetch_doc,
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    mock_fetch_doc.assert_awaited_once_with(
        "https://raw.githubusercontent.com/eslint/eslint/main/MIGRATION.md"
    )
    inv = result["investigations"]["eslint"]
    assert inv["release"]["migration_needed"] is True
    assert "MIGRATION.md" in inv["release"]["migration_guide"]
    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 3


@pytest.mark.asyncio
async def test_research_releases_node_falls_back_conservatively_on_failure():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": ("eslint", "eslint"),
        "tier": None,
    }
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM provider timeout")
    )

    with patch(
        "src.main_graph.subgraphs.remediation.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {},
            },
            config,
        )

    inv = result["investigations"]["eslint"]
    assert inv["release"]["migration_needed"] is True
    assert "research failed" in inv["release"]["breaking_changes"][0]


@pytest.mark.asyncio
async def test_research_releases_node_preserves_existing_call_sites_and_dependents():
    """select_targets_node already populated dependents/call_sites --
    research_releases_node must only overwrite `release`, not clobber them."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    target = {
        "target_dep": "eslint",
        "addresses": ["eslint"],
        "current_range": "7.0.0",
        "latest_version": "8.0.0",
        "resolved_repo": None,
        "tier": None,
    }
    existing_investigation = {
        "target_dep": "eslint",
        "dependents": ["some-consumer"],
        "call_sites": ["src/x.ts:1"],
        "release": ReleaseDigest(
            from_version="7.0.0", to_version="8.0.0", migration_needed=False
        ).model_dump(),
    }
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_decision(migration_needed=True, breaking_changes=["x"])
    )

    with patch(
        "src.main_graph.subgraphs.remediation.release_research._llm", mock_llm
    ):
        result = await research_releases_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
                "targets": {"eslint": target},
                "investigations": {"eslint": existing_investigation},
            },
            config,
        )

    inv = result["investigations"]["eslint"]
    assert inv["dependents"] == ["some-consumer"]
    assert inv["call_sites"] == ["src/x.ts:1"]
    assert inv["release"]["migration_needed"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_release_research.py -k research_releases_node -v`
Expected: FAIL -- `ImportError: cannot import name 'ReleaseResearchDecision'`

- [ ] **Step 3: Add the decision model, loop, and node to `release_research.py`**

Add imports at the top:

```python
import asyncio
import json
import textwrap
import time
import uuid
from typing import cast

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.conductor import ToolCall, ToolResult
from src.models.remediation import ReleaseDigest, RemediationTarget, TargetInvestigation
from src.utils.model_registry import AgentRole, get_role_llm
```

Add after the imports/constants, before `_resolve_public_ips`:

```python
_MAX_ITERATIONS = 4
_MAX_CONCURRENT_RESEARCH = 6
_llm = get_role_llm(AgentRole.REMEDIATION_RELEASE_RESEARCH)
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_RESEARCH)

_RESEARCH_SYSTEM_PROMPT = textwrap.dedent("""\
    You are researching what changed for a Node.js dependency upgrade so a
    migration planner can decide what needs to change in consuming code.

    Target: {target_dep}
    Upgrading from {from_version} to {to_version}.

    Use get_release_notes to read the release notes in that version
    window. If a release body references a migration/upgrade guide
    document (e.g. "see MIGRATION.md", a linked upgrade guide), use
    fetch_doc to read it -- the actual guidance is often there, not in the
    release body itself. If get_release_notes reports has_more=true and
    you have not yet found breaking-change evidence, call it again with
    the next page.

    When you have enough evidence (or have exhausted what's available),
    finalize with:
    - migration_needed: true ONLY when the notes/guide describe a breaking
      change a typical consumer would have to adapt to. A pure bug/patch/
      feature release with no consumer-facing break sets this to false
      with an empty migration_guide -- do not write commentary explaining
      that nothing is needed.
    - breaking_changes: each concrete breaking change, as a separate item.
    - migration_guide: concrete guidance grounded in what you actually
      read, not generic advice.

    Never repeat a tool call with the same arguments. After {max_iter}
    iterations, finalize regardless of what you've found.
    """).strip()


class ReleaseResearchDecision(BaseModel):
    tool_calls: list[ToolCall]
    finalize: bool
    migration_needed: bool
    migration_guide: str = ""
    breaking_changes: list[str] = Field(default_factory=list)
    reasoning: str


def _format_tool_results(results: list[ToolResult]) -> str:
    if not results:
        return "No results yet."
    parts = []
    for tr in results:
        val = f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        parts.append(f"[{tr.tool}] -> {val}")
    return "\n\n".join(parts)


async def _run_research_tool(tc: ToolCall, tool_map: dict) -> ToolResult:
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args, output={},
            error=f"unknown tool: {tc.tool}", duration_ms=0,
        )
    try:
        output = await fn.ainvoke(tc.args)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output=output if isinstance(output, dict) else {"result": output},
            error=None, duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args, output={},
            error=str(exc), duration_ms=int((time.monotonic() - start) * 1000),
        )


async def _research_loop(
    target_dep: str,
    from_version: str | None,
    to_version: str | None,
    resolved_repo: tuple[str, str] | None,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> ReleaseDigest:
    tools = [
        make_get_release_notes_tool(
            target_dep, from_version, to_version, resolved_repo,
            repo_path, container, docker_image,
        ),
        make_fetch_doc_tool(),
    ]
    tool_map = {t.name: t for t in tools}
    tool_results: list[ToolResult] = []
    system = _RESEARCH_SYSTEM_PROMPT.format(
        target_dep=target_dep,
        from_version=from_version or "unknown",
        to_version=to_version or "unknown",
        max_iter=_MAX_ITERATIONS,
    )
    structured = _llm.with_structured_output(
        ReleaseResearchDecision, method="function_calling"
    )

    try:
        decision: ReleaseResearchDecision | None = None
        for iteration in range(_MAX_ITERATIONS):
            last = iteration == _MAX_ITERATIONS - 1
            prompt = (
                f"Tool results so far:\n{_format_tool_results(tool_results)}\n\n"
                f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
            )
            decision = cast(
                ReleaseResearchDecision,
                await structured.ainvoke(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]
                ),
            )
            if decision.finalize or last:
                break
            if decision.tool_calls:
                results = await asyncio.gather(
                    *[_run_research_tool(tc, tool_map) for tc in decision.tool_calls]
                )
                tool_results.extend(results)
        assert decision is not None
        return ReleaseDigest(
            from_version=from_version,
            to_version=to_version,
            migration_needed=decision.migration_needed,
            migration_guide=decision.migration_guide,
            breaking_changes=decision.breaking_changes,
        )
    except Exception as exc:
        logger.warning(
            "_research_loop: research failed for %s: %s; defaulting to "
            "migration_needed=True (conservative)",
            target_dep,
            exc,
        )
        return ReleaseDigest(
            from_version=from_version,
            to_version=to_version,
            migration_needed=True,
            migration_guide="",
            breaking_changes=[f"research failed, assuming breaking: {exc}"],
        )


async def _research_bounded(
    target: RemediationTarget, repo_path: str, container: ContainerRunPort, docker_image: str
) -> ReleaseDigest:
    async with _semaphore:
        return await _research_loop(
            target.target_dep,
            target.current_range,
            target.latest_version,
            target.resolved_repo,
            repo_path,
            container,
            docker_image,
        )


async def research_releases_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    targets = state.get("targets") or {}
    investigations = state.get("investigations") or {}
    researchable = [
        RemediationTarget(**t) for t in targets.values() if t.get("tier") != "r3"
    ]
    if not researchable:
        return {"investigations": {}}

    digests = await asyncio.gather(
        *[
            _research_bounded(t, prep.repo_path, container, prep.docker_image)
            for t in researchable
        ]
    )

    updated: dict[str, dict] = {}
    for target, digest in zip(researchable, digests, strict=True):
        existing = investigations.get(target.target_dep)
        inv = (
            TargetInvestigation(**existing)
            if existing
            else TargetInvestigation(target_dep=target.target_dep, release=digest)
        )
        updated[target.target_dep] = inv.model_copy(update={"release": digest}).model_dump()

    return {"investigations": updated}
```

Note: `make_get_release_notes_tool` (a `@tool`-decorated function) has a
`.name` attribute of `"get_release_notes"` automatically from LangChain's
`@tool` decorator using the function name -- `tool_map = {t.name: t for t
in tools}` relies on that, same as `make_fetch_doc_tool`'s explicit
`fetch_doc_tool.name = "fetch_doc"` override from Task 5 (needed there
because the inner function is named `fetch_doc_tool`, not `fetch_doc`, to
avoid shadowing the module-level `fetch_doc` it wraps).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_release_research.py -v`
Expected: PASS -- every test in the file

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/remediation/release_research.py \
  tests/unit/subgraphs/remediation/test_release_research.py
git commit -m "feat: add research loop and research_releases_node"
```

---

### Task 8: `plan.py` — surface `breaking_changes` in the planner prompt

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/plan.py`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_plan.py`

**Interfaces:**
- No signature changes -- `_format_targets(targets: dict[str, dict],
  investigations: dict[str, dict]) -> str` output string gains one more
  line per target.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_plan.py`:

```python
from src.main_graph.subgraphs.remediation.plan import _format_targets


def test_format_targets_includes_breaking_changes():
    targets = {"eslint": {"tier": "r2", "current_range": "7.0.0"}}
    investigations = {
        "eslint": {
            "dependents": [],
            "call_sites": [],
            "release": {
                "migration_needed": True,
                "to_version": "8.0.0",
                "migration_guide": "switch to flat config",
                "breaking_changes": ["flat config replaces .eslintrc"],
            },
        }
    }
    formatted = _format_targets(targets, investigations)
    assert "breaking_changes=['flat config replaces .eslintrc']" in formatted


def test_format_targets_breaking_changes_defaults_empty_list():
    targets = {"eslint": {"tier": "r1", "current_range": "7.0.0"}}
    investigations = {}
    formatted = _format_targets(targets, investigations)
    assert "breaking_changes=[]" in formatted
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_plan.py -k format_targets -v`
Expected: FAIL -- `AssertionError` (the line isn't there yet)

- [ ] **Step 3: Edit `_format_targets`**

In `apps/backend/src/main_graph/subgraphs/remediation/plan.py`, change:

```python
        lines.append(
            f"- target_dep={dep} tier_hint={t.get('tier') or 'r1'} "
            f"current_range={t.get('current_range') or 'unknown'} "
            f"dependents={inv.get('dependents') or []} "
            f"call_sites={inv.get('call_sites') or []} "
            f"migration_needed={rel.get('migration_needed')} "
            f"to_version={rel.get('to_version') or 'unknown'} "
            f"migration_guide={rel.get('migration_guide') or 'none'}"
        )
```

to:

```python
        lines.append(
            f"- target_dep={dep} tier_hint={t.get('tier') or 'r1'} "
            f"current_range={t.get('current_range') or 'unknown'} "
            f"dependents={inv.get('dependents') or []} "
            f"call_sites={inv.get('call_sites') or []} "
            f"migration_needed={rel.get('migration_needed')} "
            f"to_version={rel.get('to_version') or 'unknown'} "
            f"migration_guide={rel.get('migration_guide') or 'none'} "
            f"breaking_changes={rel.get('breaking_changes') or []}"
        )
```

- [ ] **Step 4: Run the full `test_plan.py` to verify everything passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_plan.py -v`
Expected: PASS -- all tests in the file

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/remediation/plan.py tests/unit/subgraphs/remediation/test_plan.py
git commit -m "feat: surface breaking_changes in the migration planner's prompt"
```

---

### Task 9: Wire `select_targets_node` + `research_releases_node` into `graph.py`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/graph.py`

**Interfaces:**
- Consumes: `select_targets_node` (Task 4), `research_releases_node`
  (Task 7), `build_migration_plan_node` (`plan.py`, unchanged).

- [ ] **Step 1: Read the current wiring**

Run: `cd apps/backend && cat src/main_graph/subgraphs/remediation/graph.py`

Confirm the current node/edge list still matches what Task 4/Task 7 assume
(`classify_targets_node` as the entry node, feeding directly into
`build_migration_plan_node`) before editing -- if it doesn't, stop and
reconcile with this plan's assumptions before continuing.

- [ ] **Step 2: Edit the imports and node/edge wiring**

Replace the `classify` import:

```python
from src.main_graph.subgraphs.remediation.classify import classify_targets_node
```

with:

```python
from src.main_graph.subgraphs.remediation.release_research import (
    research_releases_node,
)
from src.main_graph.subgraphs.remediation.select_targets import select_targets_node
```

Replace every occurrence of `"classify_targets_node"`/`classify_targets_node`
in `build_remediation_subgraph()`'s node/edge registration with
`"select_targets_node"`/`select_targets_node`, and add
`research_releases_node` as a new node sitting between it and
`build_migration_plan_node`:

```python
    builder.add_node("select_targets_node", select_targets_node)
    builder.add_node("research_releases_node", research_releases_node)
    builder.add_node("build_migration_plan_node", build_migration_plan_node)
    ...
    builder.add_edge(START, "select_targets_node")
    builder.add_edge("select_targets_node", "research_releases_node")
    builder.add_edge("research_releases_node", "build_migration_plan_node")
```

(keep every other node/edge exactly as-is -- `remediate_targets_node`,
`group_and_verify_gate`, `pr_and_persist_node`, and the retry conditional
edge are untouched)

- [ ] **Step 3: Verify the module imports cleanly**

Run: `cd apps/backend && uv run python -c "from src.main_graph.subgraphs.remediation.graph import build_remediation_subgraph; build_remediation_subgraph()"`
Expected: no output, no traceback

- [ ] **Step 4: Run the full remediation unit test suite**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/ -v`
Expected: PASS for every file EXCEPT `test_deepagent_nodes.py` and
`test_model_role_tagging.py` if they reference `classify` anywhere --
if either fails, grep them for `classify` and note it; those are not
otherwise in this plan's file list, so only patch what's strictly needed
to stop them importing the deleted module (e.g. an unused import), not a
broader rewrite

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/remediation/graph.py
git commit -m "feat: wire select_targets_node and research_releases_node into remediation graph"
```

---

### Task 10: Update the remediation integration test

**Files:**
- Modify: `apps/backend/tests/subgraphs/test_remediation_subgraph.py`

**Interfaces:**
- Consumes: `select_targets.resolve_package_info`,
  `select_targets.compute_blast_radius`, `select_targets._index_codegraph`
  (all patchable module-level names in the new `select_targets.py`),
  `release_research._llm` (patchable in `release_research.py`).

- [ ] **Step 1: Replace the `_classify_everything_as_r1` fixture**

This integration test currently mocks the single `classify.classify_target`
call site to stub out tier/digest entirely. With two nodes now, replace it
with a fixture that mocks each new node's *external* boundaries (I/O calls
and the LLM), not the nodes themselves -- so both nodes run for real as
compiled graph steps, same spirit as the original fixture's docstring
intent ("proving classify_targets_node... really ran as a real step").

Replace the whole `_classify_everything_as_r1` fixture (lines 96-125) with:

```python
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
```

- [ ] **Step 2: Update the import block**

Replace:

```python
from src.models.remediation import (
    MigrationPlan,
    MigrationTask,
    ReleaseDigest,
    RemediationOutcome,
    TargetInvestigation,
)
```

with:

```python
from src.main_graph.subgraphs.remediation.release_research import (
    ReleaseResearchDecision,
)
from src.models.remediation import MigrationPlan, MigrationTask, RemediationOutcome
```

(`ReleaseDigest`/`TargetInvestigation` are no longer constructed directly
in this file now that the fixture drives the real nodes instead of
stubbing `classify_target`'s return value)

Add `MagicMock` to the existing `from unittest.mock import AsyncMock, patch`
import (becomes `from unittest.mock import AsyncMock, MagicMock, patch`).

- [ ] **Step 3: Update the fixture-dependent test and module docstring**

In `test_pure_bump_target_ships_one_fixed_pr`, replace the
`_classify_everything_as_r1` fixture parameter and its trailing assertions:

```python
async def test_pure_bump_target_ships_one_fixed_pr(
    tmp_path, result_dao, subgraph_config, _classify_everything_as_r1
):
```

becomes:

```python
async def test_pure_bump_target_ships_one_fixed_pr(
    tmp_path, result_dao, subgraph_config, _select_and_research_everything_as_clean_bump
):
```

and replace the trailing block:

```python
    # classify_targets_node's merged classify+investigate step is a real
    # step in the compiled graph -- it must have actually run for "leftpad"
    # before build_migration_plan_node's (mocked) planning call ever ran.
    _classify_everything_as_r1.assert_awaited_once()
    classified_target = _classify_everything_as_r1.await_args.args[0]
    assert classified_target.target_dep == "leftpad"
```

with:

```python
    # select_targets_node is a real step in the compiled graph -- it must
    # have actually resolved "leftpad" before build_migration_plan_node's
    # (mocked) planning call ever ran.
    _select_and_research_everything_as_clean_bump.assert_awaited_once()
    resolved_target = _select_and_research_everything_as_clean_bump.await_args.args[0]
    assert resolved_target.target_dep == "leftpad"
```

Update the module docstring's pipeline description (lines 8-11) and the
"What is mocked" bullet (lines 27-30) to describe `select_targets_node` +
`research_releases_node` in place of `classify_targets_node` /
`classify.classify_target`, following the same style as the existing prose
(what's real vs. mocked, and why).

- [ ] **Step 4: Run the integration test suite**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_remediation_subgraph.py -v`
Expected: PASS -- all four tests (`test_pure_bump_target_ships_one_fixed_pr`,
`test_requires_signal_pulls_in_a_non_finding_companion`,
`test_correction_round_retries_then_gives_up_at_cap`,
`test_consent_false_opens_zero_prs_across_every_group`). Requires Docker
per the file's own header instructions.

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add tests/subgraphs/test_remediation_subgraph.py
git commit -m "test: update remediation integration test for select_targets/research_releases split"
```

---

### Task 11: Update `docs/graphs.md`

**Files:**
- Modify: `apps/backend/docs/graphs.md`

- [ ] **Step 1: Update the remediation section header and mermaid diagram**

Change the pipeline description (currently "5-node pipeline: classify →
plan → remediate → verify → PR/persist") to "6-node pipeline: select →
research → plan → remediate → verify → PR/persist".

In the mermaid diagram, replace:

```
    START([start]) --> classify_targets_node

    classify_targets_node["classify_targets_node\n― deterministic select + codegraph + LLM tier/digest, fan-out per target ―"]
    classify_targets_node --> build_migration_plan_node
```

with:

```
    START([start]) --> select_targets_node

    select_targets_node["select_targets_node\n― deterministic select + codegraph + version/repo resolve, fan-out per target ―"]
    select_targets_node --> research_releases_node

    research_releases_node["research_releases_node\n― agentic release-note research, fan-out per non-r3 target ―"]
    research_releases_node --> build_migration_plan_node
```

and update the `classDef`/`class` lines at the bottom:

```
    class build_migration_plan_node llm
    class remediate_targets_node agent
    class classify_targets_node,group_and_verify_gate,pr_and_persist_node det
```

becomes:

```
    class build_migration_plan_node llm
    class remediate_targets_node,research_releases_node agent
    class select_targets_node,group_and_verify_gate,pr_and_persist_node det
```

(`research_releases_node` is agentic, same class as `remediate_targets_node`,
not `llm` -- it's a tool-calling loop, not a single structured-output call)

- [ ] **Step 2: Replace the `classify_targets_node` prose entry**

Replace the bullet:

```
- **`classify_targets_node`** (`classify.py`) — `select_remediation_targets` deterministically turns analysis findings into `RemediationTarget`s (dedup, direct-dep anchoring), resolves each target's registry version + GitHub repo in one `npm view` call, then fans out `classify_target` over every target (bounded concurrency, semaphore=6). Per target: `dependents_of` (deterministic, from the dependency graph), a codegraph `blast_radius` call for real call sites, a deterministic no-upgrade check (registry publishes nothing above the declared range → tier `r3`, no fetch, no LLM), and otherwise ONE LLM call over the release notes windowed to the target version that produces both the tier (`r1`/`r2`/`r3`, advisory hint only downstream) and a migration digest (`migration_needed`/`migration_guide`/`breaking_changes`) grounded in the dependents/call-sites already gathered. Writes `targets` and `investigations`, resets `remediations`.
```

with two bullets:

```
- **`select_targets_node`** (`select_targets.py`) — Deterministic, no LLM. `select_remediation_targets` turns analysis findings into `RemediationTarget`s (dedup, direct-dep anchoring), resolves each target's registry version + GitHub repo (bounded concurrency, semaphore=6), and decides the deterministic no-upgrade check (registry publishes nothing above the declared range → tier `r3`, no fetch). For every target: `dependents_of` (structural, from the dependency graph) and a codegraph `blast_radius` call for real call sites. Writes `targets` and `investigations` (the latter with a placeholder release digest `research_releases_node` fills in next), resets `remediations`.

- **`research_releases_node`** (`release_research.py`) — Agentic. For every target with `tier != "r3"` (fan-out, bounded concurrency, semaphore=6): a small structured-output loop (own `ReleaseResearchDecision`, own tools, iteration cap 4 — shape borrowed from the analysis subgraph's `_react_loop`, not `deepagents`) with `get_release_notes` (paginated GitHub releases, windowed to the upgrade range, the agent decides whether it needs another page) and `fetch_doc` (an SSRF-hardened fetch for a linked migration guide the release body points at) to produce `migration_needed`/`migration_guide`/`breaking_changes`. Overwrites only the `release` field of each target's `investigations` entry, preserving `dependents`/`call_sites` from `select_targets_node`.
```

- [ ] **Step 3: Update the state-fields paragraph if it names `classify.py`**

Run: `cd apps/backend && grep -n "classify" docs/graphs.md`
Expected: no remaining hits after Steps 1-2 -- if any remain (e.g. in the
"State fields" paragraph or elsewhere), update them to name
`select_targets_node`/`research_releases_node` as appropriate to the
surrounding sentence.

- [ ] **Step 4: Commit**

```bash
cd apps/backend && git add docs/graphs.md
git commit -m "docs: update graphs.md for select_targets_node/research_releases_node"
```

---

### Task 12: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend unit test suite**

Run: `cd apps/backend && uv run pytest tests/unit -v`
Expected: PASS. If anything outside this plan's file list fails because it
imported `classify.py` or referenced `AgentRole.REMEDIATION_CLASSIFY`,
fix only the import/reference (not a broader rewrite) and note it.

- [ ] **Step 2: Run the full backend subgraph integration suite**

Run: `cd apps/backend && uv run pytest tests/subgraphs -v`
Expected: PASS (requires Docker).

- [ ] **Step 3: Run lint/type checks if configured**

Run: `cd apps/backend && uv run ruff check . && uv run mypy src` (adjust to
whatever this repo's actual lint/typecheck commands are, per
`apps/backend/Makefile`/`pyproject.toml` -- check before running blindly)
Expected: PASS, or pre-existing failures unrelated to this change (note
which, do not fix unrelated pre-existing issues as part of this plan).

- [ ] **Step 4: Final commit if any fixups were needed**

Only if Steps 1-3 required fixes beyond what earlier tasks already
committed:

```bash
cd apps/backend && git add -A
git commit -m "fix: address full-suite verification fallout"
```
