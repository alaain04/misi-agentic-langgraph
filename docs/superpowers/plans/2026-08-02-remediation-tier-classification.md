# Remediation r1/r2/r3 Tier Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split remediation targets into r1 (minor bump) / r2 (breaking change) / r3 (dependency migration) tiers via a new cheap classification step, dispatch the existing full investigate-and-fix subagent only for r1/r2, and defer r3 (and anything coupled to it) without ever attempting it.

**Architecture:** A new `classify_targets_node` runs once, before `root_deepagent_node`, replacing that node's current inline target-selection logic. It classifies each target from GitHub release notes alone (no `npm_audit`/`npm_outdated` — see Global Constraints), settles r3 targets immediately as deferred `Remediation` records, and only lets r1/r2 targets reach the existing per-target subagent. `group_and_verify_gate` gains one new check that defers an entire connected group — wholesale, without verification — the moment any member (pre-classified or discovered mid-investigation) has `strategy == "replace"`.

**Tech Stack:** Python, LangGraph, `deepagents`, Pydantic, `langchain_core` structured output, pytest + pytest-asyncio.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-remediation-tier-classification.md` (D1-D6). Read it before starting if anything below is ambiguous.
- Scope is `apps/backend/src/main_graph/subgraphs/remediation/` only. Do not touch discovery, analysis, or report subgraphs.
- `npm_audit`/`npm_outdated` are dropped from remediation entirely (D1b) — do not call them from `classify_targets_node` or anywhere else in this scope. The functions themselves stay in `src/main_graph/tools/npm_cli.py` untouched (they're registered, general-purpose tools used elsewhere via `TOOL_REGISTRY`, not remediation-only).
- Do not remove or alter any existing `replace`-strategy plumbing (`apply_group_changes`'s replace branch, `Remediation`/`RemediationOutcome`'s `replacement_dep`/`replacement_range` fields, `_pr_title_and_body`'s "replace - review required" label) — it's unreachable today but is the foundation for future work (D4/D6).
- No PR consolidation across groups or across a whole run — PR granularity stays exactly one PR per connected group (D5).
- Every step that adds behavior needs a failing test first, then the minimal code to pass it (TDD). Run the affected test file after every step, not just at the end of a task.
- `ruff check` and `mypy` must stay green after every task, not just at the very end — fix fallout in the same task, don't defer it.

---

## File Structure

New files:
- `src/main_graph/subgraphs/remediation/changelog.py` — shared GitHub release-notes fetch (`fetch_release_notes`), extracted from `deepagent/tools.py` so both the per-target subagent's tool and the new classifier can use it without duplicating the `gh api`/`npm view` plumbing.
- `src/main_graph/subgraphs/remediation/classify.py` — `TargetClassification` model, `classify_target()` (one LLM call per target), and `classify_targets_node` (the new graph node: selection + classify + r1/r2 vs r3 split).
- `tests/unit/subgraphs/remediation/test_changelog.py` — tests for `fetch_release_notes` (moved from `test_deepagent_tools.py`).
- `tests/unit/subgraphs/remediation/test_classify.py` — tests for `classify_target` and `classify_targets_node`.

Modified files:
- `src/main_graph/subgraphs/remediation/deepagent/tools.py` — `make_read_release_notes_tool` delegates to `changelog.fetch_release_notes` instead of duplicating the fetch logic.
- `src/main_graph/subgraphs/remediation/deepagent/nodes.py` — `root_deepagent_node` drops its inline selection/evidence branch (now `classify_targets_node`'s job) and simply dispatches whatever `state["targets"]` already holds; `group_and_verify_gate` gains the defer-on-replace check.
- `src/main_graph/subgraphs/remediation/deepagent/state.py`, `src/main_graph/subgraphs/remediation/state.py`, `src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py` — drop the `evidence` field/section entirely (D1b).
- `src/main_graph/subgraphs/remediation/graph.py` — wire `classify_targets_node` in front of `root_deepagent_node`.
- `tests/unit/subgraphs/remediation/test_deepagent_tools.py`, `test_deepagent_nodes.py`, `test_deepagent_state.py`, `test_state.py`, `test_subagent_wrapper.py`, `tests/subgraphs/test_remediation_subgraph.py` — updated for the above.

---

### Task 1: Extract shared release-notes fetch into `changelog.py`

**Files:**
- Create: `src/main_graph/subgraphs/remediation/changelog.py`
- Create: `tests/unit/subgraphs/remediation/test_changelog.py`
- Modify: `src/main_graph/subgraphs/remediation/deepagent/tools.py`
- Modify: `tests/unit/subgraphs/remediation/test_deepagent_tools.py`

**Interfaces:**
- Produces: `async def fetch_release_notes(package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str) -> dict` — returns `{"package_name", "available": bool, "error"?: str}` on failure, or `{"package_name", "available": True, "repository": str, "releases": [{"tag", "name", "body"}, ...]}` on success. Consumed by Task 2's `classify_target` and by `tools.py`'s tool wrapper.

- [ ] **Step 1: Write the failing tests for `fetch_release_notes` in the new test file**

```python
# tests/unit/subgraphs/remediation/test_changelog.py
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes


class FakeContainer:
    """Returns queued (rc, stdout, stderr) per run() call, in order."""

    def __init__(self, results):
        self._results = list(results)
        self.commands = []

    async def run(
        self, image, command, volume=None, run_as_root=False, secret_env=None
    ):
        self.commands.append(command)
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_fetch_release_notes_returns_unavailable_when_repo_unresolved():
    container = FakeContainer([(1, "", "npm error 404 Not Found")])
    result = await fetch_release_notes(
        "left-pad", "/repo", container, "node:lts-alpine"
    )
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_release_notes_success():
    container = FakeContainer([(0, "git+https://github.com/eslint/eslint.git\n", "")])
    releases_json = json.dumps(
        [{"tag_name": "v9.0.0", "name": "9.0.0", "body": "breaking: flat config"}]
    ).encode()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(releases_json, b""))
    fake_proc.returncode = 0

    with patch(
        "src.main_graph.subgraphs.remediation.changelog.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    ):
        result = await fetch_release_notes(
            "eslint", "/repo", container, "node:lts-alpine"
        )

    assert result["available"] is True
    assert result["repository"] == "eslint/eslint"
    assert result["releases"][0]["tag"] == "v9.0.0"


@pytest.mark.asyncio
async def test_fetch_release_notes_safely_quotes_package_name():
    """Shell metacharacters in a package name must not reach the container
    command unescaped (command injection guard)."""
    container = FakeContainer([(1, "", "npm error 404")])
    malicious_package = "eslint; rm -rf /"
    result = await fetch_release_notes(
        malicious_package, "/repo", container, "node:lts-alpine"
    )

    assert len(container.commands) == 1
    executed_command = container.commands[0]
    assert "'eslint; rm -rf /'" in executed_command or (
        "eslint" in executed_command and "rm -rf" not in executed_command
    )
    assert result["available"] is False
```

- [ ] **Step 2: Run the tests to verify they fail with ModuleNotFoundError**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_changelog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.remediation.changelog'`

- [ ] **Step 3: Create `changelog.py`, moving the fetch logic out of `tools.py` verbatim**

```python
# src/main_graph/subgraphs/remediation/changelog.py
"""Shared GitHub release-notes fetch for an npm package -- used by the
per-target remediation subagent's read_release_notes tool
(deepagent/tools.py) and by the tier classifier (classify.py)."""

from __future__ import annotations

import asyncio
import json
import re
import shlex

from src.domain.ports.container_run_port import ContainerRunPort

_GITHUB_REPO_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?\s*$")


async def _resolve_github_repo(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> tuple[str, str] | None:
    command = f"cd /workspace && npm view {shlex.quote(package_name)} repository.url"
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


async def fetch_release_notes(
    package_name: str, repo_path: str, container: ContainerRunPort, docker_image: str
) -> dict:
    """Fetch recent GitHub release notes for an npm package, resolved via
    its registry-declared repository URL."""
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
            "gh",
            "api",
            f"repos/{owner}/{repo}/releases",
            "--paginate",
            "-q",
            ".[:20]",
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
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_changelog.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Update `tools.py` to delegate to `fetch_release_notes`, removing the now-duplicated logic and its now-unused imports**

Replace the top of `src/main_graph/subgraphs/remediation/deepagent/tools.py` (imports and the release-notes section) so it reads:

```python
from __future__ import annotations

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.discovery.dependency_graph import dependents_of
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import apply_bump


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
        return await fetch_release_notes(
            package_name, repo_path, container, docker_image
        )

    return read_release_notes
```

Everything below `make_read_release_notes_tool` in that file (`make_dependents_of_tool`, `make_bump_dependency_tool`, `make_verify_tool`) stays exactly as-is. Remove the now-dead `import asyncio`, `import json`, `import logging`, `import re`, `import shlex`, the `logger = logging.getLogger(__name__)` line (unused even before this change — verify with `grep -n "logger\." src/main_graph/subgraphs/remediation/deepagent/tools.py` returning nothing), and the `_GITHUB_REPO_RE`/`_resolve_github_repo` definitions.

- [ ] **Step 6: Update `test_deepagent_tools.py` — remove the moved tests, add a thin delegation test**

In `tests/unit/subgraphs/remediation/test_deepagent_tools.py`, delete `test_read_release_notes_returns_unavailable_when_repo_unresolved`, `test_read_release_notes_success`, and `test_read_release_notes_safely_quotes_package_name` (now covered by `test_changelog.py`). Replace them with:

```python
@pytest.mark.asyncio
async def test_read_release_notes_tool_delegates_to_fetch_release_notes():
    container = MagicMock()
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.tools.fetch_release_notes",
        AsyncMock(
            return_value={
                "package_name": "eslint",
                "available": True,
                "repository": "eslint/eslint",
                "releases": [],
            }
        ),
    ) as mock_fetch:
        tool = make_read_release_notes_tool("/repo", container, "node:lts-alpine")
        result = await tool.ainvoke({"package_name": "eslint"})

    mock_fetch.assert_awaited_once_with("eslint", "/repo", container, "node:lts-alpine")
    assert result["available"] is True
```

- [ ] **Step 7: Run the full remediation unit test directory to verify nothing broke**

Run: `uv run pytest tests/unit/subgraphs/remediation/ -v`
Expected: PASS, all tests green

- [ ] **Step 8: Commit**

```bash
git add src/main_graph/subgraphs/remediation/changelog.py \
        src/main_graph/subgraphs/remediation/deepagent/tools.py \
        tests/unit/subgraphs/remediation/test_changelog.py \
        tests/unit/subgraphs/remediation/test_deepagent_tools.py
git commit -m "refactor: extract release-notes fetch into shared changelog module"
```

---

### Task 2: Add the tier classifier (`classify_target`)

**Files:**
- Create: `src/main_graph/subgraphs/remediation/classify.py`
- Create: `tests/unit/subgraphs/remediation/test_classify.py`

**Interfaces:**
- Consumes: `fetch_release_notes` from Task 1 (`src.main_graph.subgraphs.remediation.changelog`); `RemediationTarget` from `src.models.remediation`.
- Produces: `class TargetClassification(BaseModel)` with fields `tier: Literal["r1", "r2", "r3"]`, `rationale: str`; `async def classify_target(target: RemediationTarget, repo_path: str, container: ContainerRunPort, docker_image: str) -> TargetClassification`. Consumed by Task 3's `classify_targets_node`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/subgraphs/remediation/test_classify.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.classify import (
    TargetClassification,
    classify_target,
)
from src.models.remediation import RemediationTarget


@pytest.mark.asyncio
async def test_classify_target_returns_llm_classification():
    target = RemediationTarget(
        target_dep="lodash", addresses=["lodash"], current_range="^4.17.11"
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(tier="r1", rationale="patch release only")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        result = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine"
        )

    assert result.tier == "r1"
    mock_llm.with_structured_output.assert_called_once()


@pytest.mark.asyncio
async def test_classify_target_defaults_to_r2_on_llm_exception():
    """A classification failure must degrade to a conservative default
    (r2: assume breaking, needs review) rather than crashing the whole
    classify_targets_node and thus the job."""
    target = RemediationTarget(target_dep="lodash", addresses=["lodash"])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM provider timeout")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        result = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine"
        )

    assert result.tier == "r2"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.remediation.classify'`

- [ ] **Step 3: Create `classify.py` with `TargetClassification` and `classify_target`**

```python
# src/main_graph/subgraphs/remediation/classify.py
"""Classifies each remediation target into a tier (r1/r2/r3) from its
GitHub release notes alone, before the expensive per-target investigate-
and-edit subagent ever runs. r3 (dependency migration) targets are settled
directly by classify_targets_node and never dispatched -- see
docs/superpowers/specs/2026-08-02-remediation-tier-classification.md."""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes
from src.models.remediation import RemediationTarget
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_CLASSIFY_SYSTEM_PROMPT = """\
You classify an npm dependency remediation into exactly one tier, from its \
GitHub release notes:

- r1: a same-package version bump with no breaking changes relevant to a \
typical consumer. Safe to bump without touching calling code.
- r2: a same-package version bump whose release notes describe breaking \
changes (removed/renamed APIs, changed defaults, new required config, \
major-version markers, etc.) that would plausibly require adapting calling \
code.
- r3: the release notes (or the absence of further releases) indicate this \
package is deprecated, abandoned, or explicitly superseded by a different \
package -- a same-package bump is not the right fix at all, only migrating \
to a replacement dependency is.

Prefer r1 unless the release notes give a concrete reason to classify \
otherwise. r3 is reserved for an explicit migration signal, not merely \
"has a major version"."""


class TargetClassification(BaseModel):
    tier: Literal["r1", "r2", "r3"]
    rationale: str


async def classify_target(
    target: RemediationTarget,
    repo_path: str,
    container: ContainerRunPort,
    docker_image: str,
) -> TargetClassification:
    try:
        release_notes = await fetch_release_notes(
            target.target_dep, repo_path, container, docker_image
        )
        structured = _llm.with_structured_output(
            TargetClassification, method="function_calling"
        )
        return await structured.ainvoke(
            [
                {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Dependency: {target.target_dep}\n"
                        f"Current range: {target.current_range or 'unknown'}\n"
                        f"Release notes: {json.dumps(release_notes)[:4000]}"
                    ),
                },
            ]
        )
    except Exception as exc:
        logger.warning(
            "classify_target: classification failed for %s: %s; "
            "defaulting to r2 (conservative)",
            target.target_dep,
            exc,
        )
        return TargetClassification(
            tier="r2",
            rationale=f"classification failed, defaulting conservatively: {exc}",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_classify.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/remediation/classify.py \
        tests/unit/subgraphs/remediation/test_classify.py
git commit -m "feat: add per-target r1/r2/r3 tier classifier"
```

---

### Task 3: Add `classify_targets_node`

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/classify.py`
- Modify: `tests/unit/subgraphs/remediation/test_classify.py`

**Interfaces:**
- Consumes: `classify_target` (Task 2, patched out in these tests); `select_remediation_targets` from `src.main_graph.subgraphs.remediation.selection` (unchanged); `Remediation` from `src.models.remediation`.
- Produces: `async def classify_targets_node(state: RemediationState, config: RunnableConfig) -> dict` returning `{"targets": dict[str, dict], "remediations": dict[str, dict]}` — `targets` holds only r1/r2 entries (same shape `select_remediation_targets` always produced), `remediations` holds settled r3 entries keyed by `target_dep`. Consumed by Task 6's graph wiring; read by `root_deepagent_node` (Task 4) via `state["targets"]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/subgraphs/remediation/test_classify.py
from unittest.mock import AsyncMock, MagicMock, patch

from src.main_graph.subgraphs.remediation.classify import classify_targets_node
from src.models.conductor import FindingNote
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
async def test_classify_targets_node_splits_r3_from_dispatchable_targets():
    prep = _prep(
        dependency_graph={
            "direct": {"lodash": "^4.17.11", "left-pad": "1.0.0"},
            "packages": {},
        }
    )
    analysis = MagicMock(
        findings=[
            FindingNote(dep_name="lodash", severity="high", description="d", evidence=[]),
            FindingNote(
                dep_name="left-pad", severity="high", description="d2", evidence=[]
            ),
        ]
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    async def _fake_classify(target, repo_path, container, docker_image):
        if target.target_dep == "left-pad":
            return TargetClassification(
                tier="r3", rationale="abandoned, superseded by left-pad2"
            )
        return TargetClassification(tier="r1", rationale="patch bump")

    with patch(
        "src.main_graph.subgraphs.remediation.classify.classify_target",
        AsyncMock(side_effect=_fake_classify),
    ):
        result = await classify_targets_node(
            {
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "analysis_result_id": "a-1",
                "concern": "c",
            },
            config,
        )

    assert set(result["targets"]) == {"lodash"}
    assert set(result["remediations"]) == {"left-pad"}
    r3 = result["remediations"]["left-pad"]
    assert r3["strategy"] == "replace"
    assert r3["status"] == "skipped"
    assert r3["skip_reason"] == "dependency migration - deferred, not yet supported"
    assert r3["addresses"] == ["left-pad"]


@pytest.mark.asyncio
async def test_classify_targets_node_no_findings_short_circuits():
    prep = _prep()
    analysis = MagicMock(findings=[])
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    dao.get_analysis = AsyncMock(return_value=analysis)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    result = await classify_targets_node(
        {
            "job_id": "job-1",
            "prep_result_id": "prep-1",
            "analysis_result_id": "a-1",
            "concern": "c",
        },
        config,
    )
    assert result == {"targets": {}, "remediations": {}}
```

(This appends to the same file as Task 2 — add the `pytest`, `MagicMock`, `AsyncMock`, `patch` imports at the top if not already present from Task 2's own test code; Task 2's file already imports `AsyncMock`/`MagicMock`/`patch`/`pytest`, so only add the new `FindingNote`/`PrepResult`/`classify_targets_node` imports.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_classify.py -v`
Expected: FAIL — `ImportError: cannot import name 'classify_targets_node'`

- [ ] **Step 3: Add `classify_targets_node` to `classify.py`**

First, replace `classify.py`'s import block (the top of the file, everything before `logger = logging.getLogger(__name__)`) with this full set — it adds `asyncio`, `RunnableConfig`, `get_services`, `select_remediation_targets`, `RemediationState`, `Remediation` (added alongside the already-present `RemediationTarget` on one import line), and `settings` to what Task 2 left there:

```python
from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes
from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.remediation import Remediation, RemediationTarget
from src.utils.config import settings
from src.utils.llm import Model, get_llm
```

Then append the new node function after `classify_target`:

```python
async def classify_targets_node(
    state: RemediationState, config: RunnableConfig
) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])
    analysis = await dao.get_analysis(state["analysis_result_id"])

    initial = select_remediation_targets(
        analysis.findings, prep.dependency_graph, settings.risk_min_severity
    )
    if not initial:
        return {"targets": {}, "remediations": {}}

    classifications = await asyncio.gather(
        *[
            classify_target(t, prep.repo_path, container, prep.docker_image)
            for t in initial
        ]
    )

    targets: dict[str, dict] = {}
    remediations: dict[str, dict] = {}
    for target, classification in zip(initial, classifications, strict=True):
        if classification.tier == "r3":
            remediation = Remediation(
                target_dep=target.target_dep,
                addresses=target.addresses,
                from_range=target.current_range,
                strategy="replace",
                status="skipped",
                skip_reason="dependency migration - deferred, not yet supported",
            )
            remediations[target.target_dep] = remediation.model_dump()
        else:
            targets[target.target_dep] = target.model_dump()

    return {"targets": targets, "remediations": remediations}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_classify.py -v`
Expected: PASS (4 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/remediation/classify.py \
        tests/unit/subgraphs/remediation/test_classify.py
git commit -m "feat: add classify_targets_node splitting r1/r2 dispatch from deferred r3"
```

---

### Task 4: Simplify `root_deepagent_node` and drop the `evidence` field

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/deepagent/nodes.py`
- Modify: `src/main_graph/subgraphs/remediation/deepagent/state.py`
- Modify: `src/main_graph/subgraphs/remediation/state.py`
- Modify: `src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py`
- Modify: `tests/unit/subgraphs/remediation/test_deepagent_nodes.py`
- Modify: `tests/unit/subgraphs/remediation/test_deepagent_state.py`
- Modify: `tests/unit/subgraphs/remediation/test_state.py`
- Modify: `tests/unit/subgraphs/remediation/test_subagent_wrapper.py`

**Interfaces:**
- Consumes: `state["targets"]` as already populated by `classify_targets_node` (Task 3) on the initial pass, or by `root_deepagent_node`'s own retry-synthesis on a retry pass (unchanged).
- Produces: `root_deepagent_node`'s signature/return shape is unchanged except it no longer includes an `"evidence"` key anywhere.

- [ ] **Step 1: Update the three affected tests in `test_deepagent_nodes.py` first (they'll fail against current code until Step 3 lands, which is fine for TDD -- current code doesn't yet match the new contract)**

Replace `test_root_deepagent_node_seeds_targets_from_selection_and_invokes_agent`, `test_root_deepagent_node_no_targets_short_circuits`, and `test_root_deepagent_node_recursion_limit_returns_graceful_fallback` with:

```python
@pytest.mark.asyncio
async def test_root_deepagent_node_dispatches_agent_for_seeded_targets():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes._root_deep_agent"
    ) as mock_agent:
        mock_agent.ainvoke = AsyncMock(
            return_value={
                "remediations": {
                    "lodash": {
                        "target_dep": "lodash",
                        "addresses": ["lodash"],
                        "status": "skipped",
                    }
                },
                "requires_edges": {},
            }
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

    assert result["remediations"]["lodash"]["target_dep"] == "lodash"
    mock_agent.ainvoke.assert_awaited_once()


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
    assert result["remediations"] == {}


@pytest.mark.asyncio
async def test_root_deepagent_node_recursion_limit_returns_graceful_fallback():
    """Spec D10: every bound (recursion limit, correction-round cap, group
    cap) must fail honestly instead of crashing the job. A real
    GraphRecursionError from the root deep agent's ainvoke must not
    propagate -- it must degrade to the same shape root_deepagent_node
    already returns on other paths, with remediations/requires_edges wiped
    since nothing from the aborted run is trustworthy."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.nodes._root_deep_agent"
    ) as mock_agent:
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
    assert "lodash" in result["targets"]
```

Also remove the now-unused `from src.models.conductor import FindingNote` import from the top of this test file (grep to confirm no other test in the file still uses `FindingNote` after this edit).

- [ ] **Step 2: Run the tests to verify they fail against the current implementation**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v -k root_deepagent_node`
Expected: FAIL — the current `root_deepagent_node` still calls `dao.get_analysis`, which isn't mocked in the new tests, so it raises/errors, or the assertions on `result["targets"]` mismatch.

- [ ] **Step 3: Simplify `root_deepagent_node` in `nodes.py`**

Remove `from src.main_graph.subgraphs.remediation.selection import select_remediation_targets`, `from src.main_graph.tools.npm_cli import npm_audit, npm_outdated`, and `from src.utils.config import settings` from the imports (all three become unused). Replace the body of `root_deepagent_node`:

```python
async def root_deepagent_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])

    retry_targets = state.get("retry_targets")
    if retry_targets:
        known_targets = state.get("targets") or {}
        direct = prep.dependency_graph.get("direct") or {}
        targets = {}
        for dep in retry_targets:
            if dep in known_targets:
                targets[dep] = known_targets[dep]
            else:
                # A companion target discovered mid-run purely via a
                # subagent's `requires` signal is never added to
                # state["targets"] anywhere (subagent_wrapper._run only
                # returns remediations/requires_edges). Synthesize a
                # minimal entry the same way subagent_wrapper._run does
                # for an unknown target name, so it still gets explicitly
                # redispatched in the retry round's open_list instead of
                # being silently dropped.
                targets[dep] = RemediationTarget(
                    target_dep=dep, addresses=[], current_range=direct.get(dep)
                ).model_dump()
    else:
        targets = state.get("targets") or {}

    if not targets:
        return {"targets": {}, "remediations": {}, "requires_edges": {}}

    open_list = "\n".join(
        f"- {dep} (addresses: {', '.join(t['addresses']) or 'none'})"
        for dep, t in targets.items()
    )
    initial_state = {
        "messages": [{"role": "user", "content": f"Open targets:\n{open_list}"}],
        "job_id": state["job_id"],
        "prep_result_id": state["prep_result_id"],
        "targets": targets,
        "remediations": {},
        "requires_edges": {},
    }
    run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
    try:
        result = await _root_deep_agent.ainvoke(initial_state, run_config)
    except GraphRecursionError:
        logger.warning(
            "root_deepagent_node: hit recursion_limit=%d before finishing; "
            "discarding this round's in-progress work",
            _RECURSION_LIMIT,
        )
        return {
            "targets": targets,
            "remediations": {},
            "requires_edges": {},
        }

    return {
        "targets": targets,
        "remediations": result.get("remediations") or {},
        "requires_edges": result.get("requires_edges") or {},
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v -k root_deepagent_node`
Expected: PASS

- [ ] **Step 5: Remove the `evidence` field from both state schemas**

In `src/main_graph/subgraphs/remediation/deepagent/state.py`, remove the `evidence: Annotated[dict, _keep_first_dict]` line from `RemediationDeepAgentState` (keep `_keep_first_dict` itself — `targets` still uses it).

In `src/main_graph/subgraphs/remediation/state.py`, remove `evidence: NotRequired[dict]` from `RemediationState`.

- [ ] **Step 6: Update `test_deepagent_state.py` and `test_state.py`**

In `tests/unit/subgraphs/remediation/test_deepagent_state.py`, remove `"evidence",` from the `expected_fields` tuple in `test_state_schema_declares_expected_fields`.

In `tests/unit/subgraphs/remediation/test_state.py`, remove the `"evidence": {},` line from the state dict in `test_remediation_state_accepts_new_deepagent_fields`.

- [ ] **Step 7: Drop the `evidence` section from the per-target subagent's prompt**

In `src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py`:

Remove `evidence: dict` from `_TargetSubagentState`.

Replace `_SYSTEM_PROMPT`:

```python
_SYSTEM_PROMPT = """\
You are remediating ONE dependency risk in a Node.js project: {target_dep}
(currently {current_range}).

Findings this addresses: {addresses}

Steps:
1. Call read_release_notes to review what changed between the installed
   version and reasonable upgrade candidates.
2. Call blast_radius and search_code to see how {target_dep} is actually
   used in this codebase.
3. Decide: if nothing relevant broke, bump only. If something broke but you
   can fix the call sites yourself, bump AND edit the affected files. If
   the dependency itself should be replaced (abandoned, superseded, or your
   own investigation already points that way), propose a replacement and
   migrate all usage yourself.
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
```

In `_run`, remove the `evidence=json.dumps(state.get("evidence") or {})[:4000],` line from the `system_prompt=_SYSTEM_PROMPT.format(...)` call, and remove the now-unused `import json` from the top of the file.

- [ ] **Step 8: Update `test_subagent_wrapper.py`**

Remove the `"evidence": {},` line from the state dict passed to `spec["runnable"].ainvoke(...)` in each of the 5 tests in `tests/unit/subgraphs/remediation/test_subagent_wrapper.py`.

- [ ] **Step 9: Run the full remediation unit test directory**

Run: `uv run pytest tests/unit/subgraphs/remediation/ -v`
Expected: PASS, all tests green

- [ ] **Step 10: Commit**

```bash
git add src/main_graph/subgraphs/remediation/deepagent/nodes.py \
        src/main_graph/subgraphs/remediation/deepagent/state.py \
        src/main_graph/subgraphs/remediation/state.py \
        src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py \
        tests/unit/subgraphs/remediation/test_deepagent_nodes.py \
        tests/unit/subgraphs/remediation/test_deepagent_state.py \
        tests/unit/subgraphs/remediation/test_state.py \
        tests/unit/subgraphs/remediation/test_subagent_wrapper.py
git commit -m "refactor: simplify root_deepagent_node, drop unused evidence field"
```

---

### Task 5: Defer whole groups coupled to an r3 (replace) member

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/deepagent/nodes.py`
- Modify: `tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Consumes: nothing new — reads `strategy` off the existing `remediations` dict entries already in `group_and_verify_gate`'s state.
- Produces: no new function; behavior addition inside the existing `group_and_verify_gate`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/subgraphs/remediation/test_deepagent_nodes.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v -k defers_whole_group`
Expected: FAIL — `replay_and_verify_group` gets called (the group verifies as if nothing were wrong), so `mock_replay.assert_not_called()` raises.

- [ ] **Step 3: Add the defer-on-replace check to `group_and_verify_gate`**

In `src/main_graph/subgraphs/remediation/deepagent/nodes.py`, inside the `for group in groups[:_MAX_GROUPS]:` loop, insert the new check right after the existing "missing member" block's `continue` and before `members = [Remediation(**m) for m in members_dicts]`:

```python
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

```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v -k defers_whole_group`
Expected: PASS

- [ ] **Step 5: Run the full `group_and_verify_gate` test set to confirm no regression on the mixed r1+r2 (no r3) case**

Run: `uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v -k group_and_verify_gate`
Expected: PASS, including `test_group_and_verify_gate_settles_group_once_companion_dispatched` (an eslint `bump_with_codemod` + eslint-plugin-react `bump` group with no `replace` member) — this must still verify and ship normally, proving the new check only fires when a `replace` member is actually present.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/remediation/deepagent/nodes.py \
        tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "feat: defer whole group when coupled to a dependency-migration target"
```

---

### Task 6: Wire `classify_targets_node` into the subgraph

**Files:**
- Modify: `src/main_graph/subgraphs/remediation/graph.py`
- Modify: `tests/subgraphs/test_remediation_subgraph.py`

**Interfaces:**
- Consumes: `classify_targets_node` from Task 3.
- Produces: `build_remediation_subgraph()`'s compiled graph now starts at `classify_targets_node`; external contract (`{job_id, concern, prep_result_id, analysis_result_id}` in, `{remediation_result_id}` out) is unchanged.

- [ ] **Step 1: Update `graph.py`**

```python
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.remediation.classify import classify_targets_node
from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    group_and_verify_gate,
    pr_and_persist_node,
    root_deepagent_node,
    route_after_group_verify,
)
from src.main_graph.subgraphs.remediation.state import RemediationState


def build_remediation_subgraph():
    builder = StateGraph(RemediationState)
    builder.add_node("classify_targets_node", classify_targets_node)
    builder.add_node("root_deepagent_node", root_deepagent_node)
    builder.add_node("group_and_verify_gate", group_and_verify_gate)
    builder.add_node("pr_and_persist_node", pr_and_persist_node)
    builder.add_edge(START, "classify_targets_node")
    builder.add_edge("classify_targets_node", "root_deepagent_node")
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

- [ ] **Step 2: Update the blackbox integration test file's autouse fixtures**

In `tests/subgraphs/test_remediation_subgraph.py`:

Update `_no_network_release_notes`'s patch target, since the `gh api` subprocess call moved to `changelog.py`:

```python
@pytest.fixture(autouse=True)
def _no_network_release_notes():
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"[]", b""))
    fake_proc.returncode = 0
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    ):
        yield
```

Add a second autouse fixture stubbing the new classifier so none of the five existing tests need a real LLM call — every existing scripted scenario in this file uses only `strategy="bump"`/`"bump_with_codemod"` (r1/r2), so a uniform r1 classification keeps them all dispatchable, matching today's behavior:

```python
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
```

Add the import this second fixture needs (`AsyncMock` is already imported at the top of the file; add nothing else besides the inline `TargetClassification` import shown above, kept local to the fixture to avoid an unused top-level import when reading the file's diff).

- [ ] **Step 3: Update the module docstring's "What is mocked" list and the stale comment in `test_correction_round_retries_then_gives_up_at_cap`**

In the module docstring near the top of the file, add a bullet after the existing `_extract_target_dep` bullet:

```
- `classify.classify_target` (stubbed to always return tier="r1" -- none of
  these scenarios exercise tier classification itself; that's covered by
  test_classify.py).
```

In `test_correction_round_retries_then_gives_up_at_cap`, update the comment above the `container.run` override (it currently says "Every container.run call (npm_audit/npm_outdated during the initial round, and verify_working_copy's install step during every group verification) fails" — `npm_audit`/`npm_outdated` no longer run in remediation, so simplify to):

```python
    # Every container.run call (verify_working_copy's install step during
    # every group verification) fails, so replay_and_verify_group can never
    # go green.
```

- [ ] **Step 4: Run the full blackbox suite (requires Docker)**

Run: `uv run pytest tests/subgraphs/test_remediation_subgraph.py -v`
Expected: PASS, all 5 tests green

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/remediation/graph.py \
        tests/subgraphs/test_remediation_subgraph.py
git commit -m "feat: wire classify_targets_node into the remediation subgraph"
```

---

### Task 7: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend unit + subgraph test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: PASS, no failures, no new skips beyond pre-existing ones

- [ ] **Step 2: Run ruff**

Run: `cd apps/backend && uv run ruff check .`
Expected: no errors (in particular, confirm no unused-import warnings in `tools.py`, `subagent_wrapper.py`, `nodes.py`, `test_deepagent_nodes.py`)

- [ ] **Step 3: Run mypy**

Run: `cd apps/backend && uv run mypy src`
Expected: no errors (in particular, confirm `RemediationState`/`RemediationDeepAgentState`/`_TargetSubagentState` literal dicts elsewhere in the codebase don't still reference the removed `evidence` key)

- [ ] **Step 4: Fix any fallout found in Steps 1-3 in place, re-running the affected command until green**

- [ ] **Step 5: Final commit if Step 4 produced any changes**

```bash
git add -A -- apps/backend
git commit -m "fix: address regression-pass fallout for tier classification"
```

(Skip this step entirely if Steps 1-3 were already green with no changes needed.)
