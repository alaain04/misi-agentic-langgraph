# Ingestion Subgraphs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the three-stage ingestion pipeline per spec `2026-05-17-ingestion-subgraphs-design.md` — complete Stage 1 subgraphs (registry, repo, runtime) with workers integration, update the execution model for per-dep fan-out using a `Plan` object, and scaffold Stage 2 (impact) and Stage 3 (risk_ranker, risk_score, recommendation) as stubs.

**Architecture:** `execution_planner` generates `list[list[dict]]` stages where each dict is `{"subgraph": str, "dep_name": str | None}`. SBOM-level subgraphs (vulnerabilities, license_compliance) emit a single entry; per-dep subgraphs (registry, repo, runtime) emit one entry per dependency. After Stage 1, `risk_ranker` (stub) selects high-risk deps and optionally extends `execution_stages` with Stage 2 (impact) entries. Stage 3 nodes (risk_score, recommendation) always run sequentially after Stage 2. All agentic Stage 2/3 logic is stubbed — full implementation follows separate specs.

**Tech Stack:** Python 3.12, LangGraph, Motor (async MongoDB), httpx, pytest + pytest-asyncio, uv

---

## File Map

**Create:**
- `apps/backend/src/main_graph/plan.py` — Plan TypedDict
- `apps/backend/src/utils/workers_client.py` — HTTP polling for workers API
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_utils.py` — VCS URL + version helpers
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/constants.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/graph.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/__init__.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/__init__.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/` (all files — new subgraph)
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/constants.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/graph.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/__init__.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/__init__.py`
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/` (stub — all files)
- `apps/backend/src/main_graph/nodes/risk_ranker.py` — stub node + router
- `apps/backend/src/main_graph/nodes/risk_score.py` — stub node
- `apps/backend/src/main_graph/nodes/recommendation.py` — stub node
- `tests/unit/utils/test_workers_client.py`
- `tests/unit/utils/test_sbom_utils.py`
- `tests/unit/nodes/test_execution_planner.py`

**Modify:**
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py` — add dependency_name
- `apps/backend/src/main_graph/state.py` — Plan type + new stage/ranking fields
- `apps/backend/src/utils/config.py` — add workers_url
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/analyze.py` — use dependency_name + SBOM
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/analyze.py` — workers + SBOM, drop commits
- `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py` — register all subgraphs
- `apps/backend/src/main_graph/nodes/planner.py` — produce Plan object
- `apps/backend/src/main_graph/nodes/orchestrator.py` — handle Plan object
- `apps/backend/src/main_graph/nodes/execution_planner.py` — per-dep fan-out
- `apps/backend/src/main_graph/nodes/task_dispatcher.py` — read dep_name from entries
- `apps/backend/src/main_graph/nodes/execute_plan.py` — pass dep_name + fix typo
- `apps/backend/src/main_graph/nodes/stage_advance.py` — add RISK_RANKER/RISK_SCORE routing
- `apps/backend/src/main_graph/nodes/__init__.py` — export new nodes
- `apps/backend/src/main_graph/constants.py` — add RISK_RANKER, RISK_SCORE, RECOMMENDATION
- `apps/backend/src/main_graph/graph.py` — add new nodes + routing edges
- `apps/backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py` — aggregate Stage 3 artifacts
- `tests/unit/nodes/test_planner.py` — update for Plan object return type

---

### Task 1: Foundation — Plan TypedDict, AnalysisState, MainState

**Files:**
- Create: `apps/backend/src/main_graph/plan.py`
- Modify: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py`
- Modify: `apps/backend/src/main_graph/state.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_plan.py
from src.main_graph.plan import Plan
from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


def test_plan_requires_subgraphs():
    p: Plan = {"subgraphs": ["vulnerabilities"], "dep_filter": None}
    assert p["subgraphs"] == ["vulnerabilities"]
    assert p["dep_filter"] is None


def test_plan_dep_filter_optional():
    p: Plan = {"subgraphs": ["registry"]}
    assert "dep_filter" not in p


def test_analysis_state_accepts_dependency_name():
    s: AnalysisState = {
        "sbom_cyclonedx": {},
        "discovery_summary": "ok",
        "concern": "security",
        "dependency_name": "lodash",
    }
    assert s["dependency_name"] == "lodash"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/test_plan.py -v
```
Expected: `ModuleNotFoundError` for `plan`

- [ ] **Step 3: Create `apps/backend/src/main_graph/plan.py`**

```python
from typing import NotRequired
from typing_extensions import TypedDict


class Plan(TypedDict):
    subgraphs: list[str]
    dep_filter: NotRequired[list[str] | None]
```

- [ ] **Step 4: Update `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py`**

```python
from typing import Any, NotRequired
from typing_extensions import TypedDict


class AnalysisState(TypedDict):
    sbom_cyclonedx: dict[str, Any]
    discovery_summary: str
    concern: str
    upstream_results: NotRequired[dict[str, Any]]
    repo_path: NotRequired[str]
    dependency_name: NotRequired[str]
```

- [ ] **Step 5: Update `apps/backend/src/main_graph/state.py`**

Replace the `plan` field and add new fields. The full updated file:

```python
# backend/src/main_graph/state.py
"""State schemas for the main graph."""

import operator
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.plan import Plan
from src.main_graph.subgraphs.discovery.state import ProjectMetadata


class MainState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    repo_url: str
    concern: str
    job_id: str

    # ── Discovery outputs ────────────────────────────────────────────────────
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # ── Orchestrator ─────────────────────────────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Approved plan ────────────────────────────────────────────────────────
    plan: NotRequired[Plan]
    dep_filter: NotRequired[list[str] | None]

    # ── Staged execution ─────────────────────────────────────────────────────
    execution_stages: NotRequired[list[list[dict]]]
    current_stage_index: NotRequired[int]

    # ── Parallel reducer ─────────────────────────────────────────────────────
    subgraph_results: Annotated[list[dict], operator.add]

    # ── Temp fields set via Send ─────────────────────────────────────────────
    subgraph_name: NotRequired[str]
    dep_name: NotRequired[str | None]
    upstream_results: NotRequired[dict]

    # ── Stage 1→2 gate ───────────────────────────────────────────────────────
    risk_ranker_done: NotRequired[bool]
    risk_rankings: NotRequired[list[dict]]
    high_risk_deps: NotRequired[list[str]]

    # ── Stage 3 outputs ──────────────────────────────────────────────────────
    risk_scores: NotRequired[list[dict]]
    recommendations: NotRequired[list[dict]]

    # ── Report ───────────────────────────────────────────────────────────────
    analysis_report: NotRequired[dict[str, Any]]
    reviewer_feedback: NotRequired[str]
    review_approved: NotRequired[bool]
    review_iterations: NotRequired[int]
    cancelled: NotRequired[bool]
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd apps/backend && uv run pytest tests/unit/test_plan.py -v
```
Expected: 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add apps/backend/src/main_graph/plan.py \
        apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py \
        apps/backend/src/main_graph/state.py \
        apps/backend/tests/unit/test_plan.py
git commit -m "feat: add Plan TypedDict, dependency_name to AnalysisState, update MainState"
```

---

### Task 2: Workers Client + Config

**Files:**
- Create: `apps/backend/src/utils/workers_client.py`
- Modify: `apps/backend/src/utils/config.py`
- Create: `tests/unit/utils/test_workers_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/utils/test_workers_client.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


@pytest.mark.asyncio
async def test_ingest_and_wait_returns_job_ids():
    mock_ingest_resp = MagicMock()
    mock_ingest_resp.json.return_value = {"job_ids": {"npm": "job-1"}}
    mock_ingest_resp.raise_for_status = MagicMock()

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"status": "done"}
    mock_status_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_ingest_resp)
    mock_client.get = AsyncMock(return_value=mock_status_resp)

    with patch("src.utils.workers_client.httpx.AsyncClient", return_value=mock_client):
        with patch("src.utils.workers_client.asyncio.sleep", new_callable=AsyncMock):
            from src.utils.workers_client import ingest_and_wait
            result = await ingest_and_wait(["npm"], ["lodash"])

    assert result == {"npm": "job-1"}


@pytest.mark.asyncio
async def test_ingest_and_wait_raises_on_failure():
    mock_ingest_resp = MagicMock()
    mock_ingest_resp.json.return_value = {"job_ids": {"npm": "job-2"}}
    mock_ingest_resp.raise_for_status = MagicMock()

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"status": "failed"}
    mock_status_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_ingest_resp)
    mock_client.get = AsyncMock(return_value=mock_status_resp)

    with patch("src.utils.workers_client.httpx.AsyncClient", return_value=mock_client):
        with patch("src.utils.workers_client.asyncio.sleep", new_callable=AsyncMock):
            from src.utils.workers_client import ingest_and_wait
            with pytest.raises(RuntimeError, match="failed"):
                await ingest_and_wait(["npm"], ["lodash"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/backend && uv run pytest tests/unit/utils/test_workers_client.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Add `workers_url` to config**

In `apps/backend/src/utils/config.py`, add one line inside the `Settings` class:

```python
    workers_url: str = "http://localhost:8001"
```

- [ ] **Step 4: Create `apps/backend/src/utils/workers_client.py`**

```python
"""Workers API client — submit ingest jobs and poll until complete."""

import asyncio
import logging

import httpx

from src.utils.config import settings

_log = logging.getLogger(__name__)


async def ingest_and_wait(
    entity_types: list[str],
    items: list[str],
    max_wait: float = 30.0,
) -> dict[str, str]:
    """Submit an ingest job and poll until all entity_types complete.

    Returns a mapping of entity_type → job_id for all completed jobs.
    Raises RuntimeError if any job fails or the total wait exceeds max_wait.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.workers_url}/ingest",
            json={"entity_types": entity_types, "items": items},
            timeout=10.0,
        )
        resp.raise_for_status()
        job_ids: dict[str, str] = resp.json()["job_ids"]

    await _poll_all(job_ids, max_wait)
    return job_ids


async def _poll_all(job_ids: dict[str, str], max_wait: float) -> None:
    pending = dict(job_ids)
    elapsed = 0.0
    delay = 1.0

    while pending and elapsed < max_wait:
        await asyncio.sleep(delay)
        elapsed += delay
        delay = min(delay * 1.5, 5.0)

        done: list[str] = []
        async with httpx.AsyncClient() as client:
            for entity_type, job_id in pending.items():
                resp = await client.get(
                    f"{settings.workers_url}/status/{job_id}", timeout=5.0
                )
                resp.raise_for_status()
                status = resp.json()["status"]
                if status == "done":
                    done.append(entity_type)
                elif status == "failed":
                    raise RuntimeError(
                        f"Workers job {job_id} ({entity_type}) failed"
                    )

        for et in done:
            del pending[et]

    if pending:
        raise RuntimeError(
            f"Workers jobs timed out after {max_wait}s: {list(pending.keys())}"
        )
```

- [ ] **Step 5: Add httpx dependency**

```bash
cd apps/backend && uv add httpx
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd apps/backend && uv run pytest tests/unit/utils/test_workers_client.py -v
```
Expected: 2 tests PASS

- [ ] **Step 7: Commit**

```bash
git add apps/backend/src/utils/workers_client.py \
        apps/backend/src/utils/config.py \
        apps/backend/tests/unit/utils/test_workers_client.py \
        apps/backend/pyproject.toml apps/backend/uv.lock
git commit -m "feat: add workers HTTP client with ingest/poll pattern"
```

---

### Task 3: SBOM Utilities + Complete Runtime Subgraph

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_utils.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/constants.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/__init__.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/graph.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/__init__.py`
- Modify: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/analyze.py`
- Create: `tests/unit/utils/test_sbom_utils.py`

- [ ] **Step 1: Write failing SBOM utility tests**

```python
# tests/unit/utils/test_sbom_utils.py
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_utils import (
    get_vcs_url,
    get_component_version,
    parse_github_owner_repo,
)

_SBOM = {
    "components": [
        {
            "name": "lodash",
            "version": "4.17.21",
            "externalReferences": [
                {"type": "website", "url": "https://lodash.com"},
                {"type": "vcs", "url": "https://github.com/lodash/lodash"},
            ],
        },
        {
            "name": "react",
            "version": "18.2.0",
            "externalReferences": [],
        },
    ]
}


def test_get_vcs_url_found():
    assert get_vcs_url(_SBOM, "lodash") == "https://github.com/lodash/lodash"


def test_get_vcs_url_not_found():
    assert get_vcs_url(_SBOM, "react") is None


def test_get_vcs_url_missing_dep():
    assert get_vcs_url(_SBOM, "unknown") is None


def test_get_component_version():
    assert get_component_version(_SBOM, "lodash") == "4.17.21"
    assert get_component_version(_SBOM, "unknown") is None


def test_parse_github_owner_repo_https():
    assert parse_github_owner_repo("https://github.com/lodash/lodash") == ("lodash", "lodash")


def test_parse_github_owner_repo_git_suffix():
    assert parse_github_owner_repo("git+https://github.com/expressjs/express.git") == ("expressjs", "express")


def test_parse_github_owner_repo_invalid():
    assert parse_github_owner_repo("https://gitlab.com/foo/bar") is None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd apps/backend && uv run pytest tests/unit/utils/test_sbom_utils.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_utils.py`**

```python
"""Helpers for extracting data from CycloneDX SBOM structures."""

from __future__ import annotations

import re


def get_vcs_url(sbom: dict, dep_name: str) -> str | None:
    """Return the VCS URL for a component from its externalReferences, or None."""
    for component in sbom.get("components", []):
        if component.get("name") == dep_name:
            for ref in component.get("externalReferences", []):
                if ref.get("type") == "vcs":
                    return ref.get("url")
    return None


def get_component_version(sbom: dict, dep_name: str) -> str | None:
    """Return the installed version of a component from the SBOM, or None."""
    for component in sbom.get("components", []):
        if component.get("name") == dep_name:
            return component.get("version")
    return None


def parse_github_owner_repo(url: str) -> tuple[str, str] | None:
    """Parse a GitHub URL into (owner, repo). Returns None if not a GitHub URL."""
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url)
    if match:
        return match.group(1), match.group(2)
    return None
```

- [ ] **Step 4: Run SBOM tests to verify they pass**

```bash
cd apps/backend && uv run pytest tests/unit/utils/test_sbom_utils.py -v
```
Expected: 7 tests PASS

- [ ] **Step 5: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/constants.py`**

```python
ANALYZE = "analyze"
```

- [ ] **Step 6: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.nodes.analyze import analyze

__all__ = ["analyze"]
```

- [ ] **Step 7: Update `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/nodes/analyze.py`**

Replace the `direct_dependencies` / `upstream_results["registry"]` logic with `dependency_name` + SBOM lookup. Only the top of the `analyze` function changes; the Docker execution logic below stays identical.

```python
"""Analyze node for the Runtime subgraph.

Downloads the dependency's source code, identifies quality scripts from
package.json, executes them inside Docker, and maps results to domain models.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import (
    runtime_cache_dao,
    runtime_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.models import (
    LintResult,
    RuntimeCacheEntry,
    RuntimeEntry,
    TestResult,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.tools.dependency_tools import (
    clone_github_repo,
    read_package_json,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.tools.docker_tools import (
    run_docker_install,
    run_docker_script,
)
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_utils import (
    get_component_version,
    get_vcs_url,
    parse_github_owner_repo,
)
from src.utils.config import settings
from src.utils.llm import Model, get_llm, parse_llm_json

_log = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_DOWNLOAD_TOOLS = [clone_github_repo]
_IDENTIFY_TOOLS = [read_package_json]
_EXECUTE_TOOLS = [run_docker_install, run_docker_script]

_DOWNLOAD_PROMPT = """\
You are a tool-calling agent that downloads npm package source code from GitHub.
Given a package name, version, and repository URL, call clone_github_repo with
the repository URL, version, and destination directory.
Return a JSON object with keys: resolved_version, error.
If cloning fails, set error to the error message and resolved_version to null.\
"""

_IDENTIFY_PROMPT = """\
You are a code quality script classifier for npm packages.
Given a package directory, read package.json and identify AT MOST 3 quality scripts.

INCLUDE: test, test:*, tests, audit, check, check:*, validate, verify
EXCLUDE: start, serve, dev, watch, build, bundle, compile, deploy, publish,
         install, docs, storybook, format, fmt, lint, typecheck, tsc

Return ONLY a JSON object: { "quality_scripts": ["name1", "name2"] }\
"""

_EXECUTE_PROMPT = """\
You are a tool-calling agent that executes npm scripts inside Docker containers.
1. Call run_docker_install once to install dependencies.
2. For each script, call run_docker_script to execute it.
   Continue to the next script even if one fails.

Return a JSON object:
{
  "results": {
    "script-name": {
      "script_name": "script-name",
      "exit_code": 0, "stdout": "...", "stderr": "...",
      "duration_seconds": 1.23, "timed_out": false
    }
  },
  "error": null
}\
"""


@dataclass
class _ScriptResult:
    script_name: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = field(default=False)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


async def _run_agent(
    llm_with_tools, tools, system_prompt: str, user_msg: str, max_turns: int
) -> dict | None:
    tool_map = {t.name: t for t in tools}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]
    for _ in range(max_turns):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)
        if not response.tool_calls:
            try:
                return parse_llm_json(response.content or "")
            except Exception:
                return None
        for tc in response.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            if tool_fn:
                if hasattr(tool_fn, "ainvoke"):
                    tool_result = await tool_fn.ainvoke(tc["args"])
                else:
                    tool_result = tool_fn.invoke(tc["args"])
                messages.append(
                    ToolMessage(content=str(tool_result), tool_call_id=tc["id"])
                )
    return None


def _map_to_runtime_entry(
    results: dict[str, _ScriptResult],
) -> tuple[TestResult | None, LintResult | None]:
    test_scripts = [
        r for name, r in results.items() if name.startswith("test") or name == "tests"
    ]
    lint_scripts = [r for name, r in results.items() if name.startswith("lint")]

    test_result: TestResult | None = None
    if test_scripts:
        passed = sum(1 for r in test_scripts if r.passed)
        failed = len(test_scripts) - passed
        errors = [r.stderr[:300] for r in test_scripts if not r.passed and r.stderr]
        test_result = TestResult(passed=passed, failed=failed, errors=errors)

    lint_result: LintResult | None = None
    if lint_scripts:
        errors_count = sum(1 for r in lint_scripts if not r.passed)
        lint_result = LintResult(errors=errors_count)

    return test_result, lint_result


async def analyze(state: RuntimeState) -> dict:
    dep_name = state.get("dependency_name", "")
    if not dep_name:
        result_id = await runtime_dao.save(RuntimeEntry())
        return {"result_id": result_id}

    sbom = state.get("sbom_cyclonedx", {})
    version_spec = (get_component_version(sbom, dep_name) or "").lstrip("^~>=< ")
    repository_url = get_vcs_url(sbom, dep_name) or ""

    if not repository_url:
        _log.warning("runtime.analyze: no VCS URL in SBOM for %s", dep_name)
        result_id = await runtime_dao.save(RuntimeEntry())
        return {"result_id": result_id}

    cached = await runtime_cache_dao.find_cached_entry(
        dep_name, version_spec, settings.runtime_cache_max_age_days
    )
    if cached is not None:
        result_id = await runtime_dao.save(cached.entry)
        _log.info(
            "runtime.analyze: cache hit for %s@%s, result_id=%s",
            dep_name,
            version_spec,
            result_id,
        )
        return {"result_id": result_id}

    base_tmp = os.path.expanduser("~/tmp")
    os.makedirs(base_tmp, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="npm_qa_", dir=base_tmp)

    try:
        download_llm = _llm.bind_tools(_DOWNLOAD_TOOLS)
        identify_llm = _llm.bind_tools(_IDENTIFY_TOOLS)
        execute_llm = _llm.bind_tools(_EXECUTE_TOOLS)

        download_result = await _run_agent(
            download_llm,
            _DOWNLOAD_TOOLS,
            _DOWNLOAD_PROMPT,
            (
                f"Download '{dep_name}' version '{version_spec}'"
                f" from '{repository_url}' into '{tmp_dir}'."
            ),
            max_turns=6,
        )
        if not download_result or download_result.get("error"):
            err = (download_result or {}).get("error", "download agent did not converge")
            _log.warning("runtime.analyze: download failed: %s", err)
            result_id = await runtime_dao.save(RuntimeEntry())
            return {"result_id": result_id}

        identify_result = await _run_agent(
            identify_llm,
            _IDENTIFY_TOOLS,
            _IDENTIFY_PROMPT,
            f"Read package.json from '{tmp_dir}' and identify quality scripts.",
            max_turns=4,
        )
        script_names: list[str] = (identify_result or {}).get("quality_scripts", [])

        if not script_names:
            _log.info("runtime.analyze: no quality scripts identified for %s", dep_name)
            result_id = await runtime_dao.save(RuntimeEntry())
            return {"result_id": result_id}

        script_list = ", ".join(f"'{s}'" for s in script_names)
        execute_result = await _run_agent(
            execute_llm,
            _EXECUTE_TOOLS,
            _EXECUTE_PROMPT,
            (
                f"Package directory: '{tmp_dir}'.\n"
                f"Quality scripts to run: [{script_list}].\n"
                f"Docker image: '{settings.node_docker_image}', "
                f"memory: '{settings.docker_memory_limit}', "
                f"cpu: {settings.docker_cpu_limit}, "
                f"timeout per script: {settings.script_timeout_seconds}s."
            ),
            max_turns=len(script_names) + 6,
        )

        results: dict[str, _ScriptResult] = {}
        if execute_result:
            for name, raw in (execute_result.get("results") or {}).items():
                results[name] = _ScriptResult(
                    script_name=name,
                    exit_code=raw.get("exit_code", -1),
                    stdout=raw.get("stdout", ""),
                    stderr=raw.get("stderr", ""),
                    duration_seconds=raw.get("duration_seconds", 0.0),
                    timed_out=raw.get("timed_out", False),
                )

        test_result, lint_result = _map_to_runtime_entry(results)
        entry = RuntimeEntry(test_results=test_result, lint_results=lint_result)

        try:
            await runtime_cache_dao.upsert_cached_entry(
                RuntimeCacheEntry(
                    package_name=dep_name,
                    package_version=version_spec,
                    fetched_at=datetime.now(UTC),
                    entry=entry,
                )
            )
        except Exception:
            _log.warning(
                "runtime.analyze: cache write failed for %s@%s", dep_name, version_spec
            )

        result_id = await runtime_dao.save(entry)
        _log.info(
            "runtime.analyze: saved result_id=%s scripts_run=%d",
            result_id,
            len(results),
        )
        return {"result_id": result_id}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 8: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/graph.py`**

```python
"""Runtime subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.runtime.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState

GRAPH_NAME = "runtime"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Clones dependency source code, executes its test and audit"
        " scripts inside Docker, and reports pass rate and lint error count"
    )


def build_runtime_subgraph() -> StateGraph:
    builder = StateGraph(RuntimeState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


runtime_subgraph = build_runtime_subgraph()
```

- [ ] **Step 9: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.graph import (
    DEPENDS_ON,
    GRAPH_NAME,
    build_runtime_subgraph,
    describe,
    runtime_subgraph,
)
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState

subgraph = runtime_subgraph

__all__ = [
    "DEPENDS_ON",
    "GRAPH_NAME",
    "build_runtime_subgraph",
    "describe",
    "runtime_subgraph",
    "subgraph",
    "RuntimeState",
]
```

- [ ] **Step 10: Verify import works**

```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs import runtime; print(runtime.GRAPH_NAME)"
```
Expected: `runtime`

- [ ] **Step 11: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_utils.py \
        apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/runtime/ \
        apps/backend/tests/unit/utils/test_sbom_utils.py
git commit -m "feat: add sbom_utils, complete runtime subgraph with dependency_name"
```

---

### Task 4: New Registry Subgraph

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/` (all files)

The registry subgraph calls the workers API for npm metadata, reads from the shared `npm_package_cache` MongoDB collection, and saves extracted fields to `registry_results`.

- [ ] **Step 1: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/models.py`**

```python
from pydantic import BaseModel


class RegistryEntry(BaseModel):
    dep_name: str = ""
    last_publish: str | None = None
    weekly_downloads: int | None = None
    is_deprecated: bool = False
    maintainers_count: int | None = None
```

- [ ] **Step 2: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/state.py`**

```python
from typing import NotRequired

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RegistryState(AnalysisState):
    result_id: NotRequired[str]
```

- [ ] **Step 3: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/dao.py`**

```python
from bson import ObjectId

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.registry.models import RegistryEntry


class RegistryDAO:
    @property
    def _col(self):
        return get_db()["registry_results"]

    async def save(self, entry: RegistryEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


registry_dao = RegistryDAO()
```

- [ ] **Step 4: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/constants.py`**

```python
ANALYZE = "analyze"
```

- [ ] **Step 5: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/nodes/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.registry.nodes.analyze import analyze

__all__ = ["analyze"]
```

- [ ] **Step 6: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/nodes/analyze.py`**

```python
"""Analyze node for the Registry subgraph.

Triggers npm metadata ingestion via the workers service, then reads the
result from the shared npm_package_cache MongoDB collection.
"""

from __future__ import annotations

import logging

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
from src.main_graph.subgraphs.ingestion_subgraphs.registry.models import RegistryEntry
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState
from src.utils.workers_client import ingest_and_wait

_log = logging.getLogger(__name__)


def _extract_entry(dep_name: str, doc: dict) -> RegistryEntry:
    """Extract RegistryEntry fields from an npm_package_cache document.

    The workers npm fetcher stores the full npm registry response.
    Fields: doc["time"]["modified"], doc["dist-tags"]["latest"],
    doc["deprecated"], doc["maintainers"], doc["downloads"]["weekly"]
    (downloads may be a nested object if fetched separately).
    Adjust field paths here if the workers cache schema differs.
    """
    time_data = doc.get("time") or {}
    last_publish = time_data.get("modified")

    downloads = doc.get("downloads") or {}
    weekly_downloads: int | None = None
    if isinstance(downloads, dict):
        weekly_downloads = downloads.get("weekly") or downloads.get("last-week")
    elif isinstance(downloads, int):
        weekly_downloads = downloads

    deprecated = doc.get("deprecated")
    is_deprecated = bool(deprecated)

    maintainers = doc.get("maintainers") or []
    maintainers_count = len(maintainers) if isinstance(maintainers, list) else None

    return RegistryEntry(
        dep_name=dep_name,
        last_publish=last_publish,
        weekly_downloads=weekly_downloads,
        is_deprecated=is_deprecated,
        maintainers_count=maintainers_count,
    )


async def analyze(state: RegistryState) -> dict:
    dep_name = state.get("dependency_name", "")
    if not dep_name:
        result_id = await registry_dao.save(RegistryEntry())
        return {"result_id": result_id}

    npm_cache = get_db()["npm_package_cache"]

    # Check cache before triggering workers
    cached_doc = await npm_cache.find_one({"name": dep_name})
    if cached_doc is None:
        try:
            await ingest_and_wait(["npm"], [dep_name])
        except Exception as exc:
            _log.warning("registry.analyze: workers ingest failed for %s: %s", dep_name, exc)
            result_id = await registry_dao.save(RegistryEntry(dep_name=dep_name))
            return {"result_id": result_id}
        cached_doc = await npm_cache.find_one({"name": dep_name})

    if cached_doc is None:
        _log.warning("registry.analyze: no npm data found for %s after ingest", dep_name)
        result_id = await registry_dao.save(RegistryEntry(dep_name=dep_name))
        return {"result_id": result_id}

    entry = _extract_entry(dep_name, cached_doc)
    result_id = await registry_dao.save(entry)
    _log.info("registry.analyze: saved %s, result_id=%s", dep_name, result_id)
    return {"result_id": result_id}
```

- [ ] **Step 7: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/graph.py`**

```python
"""Registry subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.registry.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.registry.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState

GRAPH_NAME = "registry"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Fetches npm registry metadata for one dependency:"
        " last publish date, weekly downloads, deprecation status, and maintainer count"
    )


def build_registry_subgraph() -> StateGraph:
    builder = StateGraph(RegistryState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


registry_subgraph = build_registry_subgraph()
```

- [ ] **Step 8: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
from src.main_graph.subgraphs.ingestion_subgraphs.registry.graph import (
    DEPENDS_ON,
    GRAPH_NAME,
    build_registry_subgraph,
    describe,
    registry_subgraph,
)
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState

subgraph = registry_subgraph

__all__ = [
    "DEPENDS_ON",
    "GRAPH_NAME",
    "build_registry_subgraph",
    "describe",
    "registry_dao",
    "registry_subgraph",
    "subgraph",
    "RegistryState",
]
```

- [ ] **Step 9: Verify import**

```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs import registry; print(registry.GRAPH_NAME)"
```
Expected: `registry`

- [ ] **Step 10: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/
git commit -m "feat: add registry subgraph with workers npm integration"
```

---

### Task 5: Rework Repo Subgraph

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/analyze.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/__init__.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/constants.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/graph.py`
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/__init__.py`

Key changes to analyze.py:
- Extract `owner/repo` from SBOM externalReferences (not `upstream_results["registry"]`)
- Call workers API for `github_issues`, `github_releases`, `github_advisories`
- Read from workers cache collections: `github_issues_cache`, `github_releases_cache`, `github_advisories_cache`
- Drop commits entirely (no curator call, no `commits` in Repository)

- [ ] **Step 1: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/constants.py`**

```python
ANALYZE = "analyze"
```

- [ ] **Step 2: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.analyze import analyze

__all__ = ["analyze"]
```

- [ ] **Step 3: Replace `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/nodes/analyze.py`**

```python
"""Analyze node for the Repo subgraph.

Fetches GitHub data (issues, releases, advisories) via the workers service,
curates each entity type with LLM agents, then persists the result.
Commits are not analyzed — GitHub commit data is not provided by workers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import (
    repo_cache_dao,
    repo_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.models import (
    Issue,
    Release,
    RepoCacheEntry,
    RepoEntry,
    Repository,
    Vulnerability,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.issues import (
    make_issue_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.releases import (
    make_release_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes.curators.vulnerabilities import (
    make_vulnerability_curation_agent,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState
from src.main_graph.subgraphs.ingestion_subgraphs.sbom_utils import (
    get_vcs_url,
    parse_github_owner_repo,
)
from src.utils.config import settings
from src.utils.workers_client import ingest_and_wait

_log = logging.getLogger(__name__)


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


async def _read_workers_cache(collection: str, owner: str, repo: str) -> list[dict]:
    """Read raw data from a workers cache collection keyed by 'owner/repo'."""
    col = get_db()[collection]
    doc = await col.find_one({"name": f"{owner}/{repo}"})
    if doc is None:
        return []
    # Workers stores the fetched list under 'items' or directly as the response array.
    # Adjust if the workers adapter stores data differently.
    data = doc.get("items") or doc.get("data") or []
    return data if isinstance(data, list) else []


async def analyze(state: RepoState) -> dict:
    dep_name = state.get("dependency_name", "")
    sbom = state.get("sbom_cyclonedx", {})

    vcs_url = get_vcs_url(sbom, dep_name) if dep_name else None
    parsed = parse_github_owner_repo(vcs_url) if vcs_url else None

    if not parsed:
        _log.warning("repo.analyze: no GitHub VCS URL in SBOM for %s", dep_name)
        result_id = await repo_dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    owner, name = parsed
    url = vcs_url or ""

    # Cache check
    cached = await repo_cache_dao.find_cached_entry(
        owner, name, settings.lookback_days, settings.repo_cache_max_age_days
    )
    if cached is not None:
        result_id = await repo_dao.save(cached.entry)
        _log.info("repo.analyze: cache hit for %s/%s, result_id=%s", owner, name, result_id)
        return {"result_id": result_id}

    # Trigger workers for GitHub data
    try:
        await ingest_and_wait(
            entity_types=["github_issues", "github_releases", "github_advisories"],
            items=[f"{owner}/{name}"],
        )
    except Exception as exc:
        _log.warning("repo.analyze: workers ingest failed for %s/%s: %s", owner, name, exc)
        result_id = await repo_dao.save(RepoEntry(repositories=[]))
        return {"result_id": result_id}

    # Read raw data from workers cache collections
    raw_issues = await _read_workers_cache("github_issues_cache", owner, name)
    raw_releases = await _read_workers_cache("github_releases_cache", owner, name)
    raw_vulns = await _read_workers_cache("github_advisories_cache", owner, name)

    batch_size = settings.reviewer_batch_size

    # Curate each entity type
    try:
        curated_issues = await make_issue_curation_agent().curate(raw_issues, batch_size)
    except Exception as exc:
        _log.warning("repo.analyze: issue curation failed: %s", exc)
        curated_issues = raw_issues

    try:
        curated_releases = await make_release_curation_agent().curate(raw_releases, batch_size)
    except Exception as exc:
        _log.warning("repo.analyze: release curation failed: %s", exc)
        curated_releases = raw_releases

    try:
        curated_vulns = await make_vulnerability_curation_agent().curate(raw_vulns, batch_size)
    except Exception as exc:
        _log.warning("repo.analyze: vuln curation failed: %s", exc)
        curated_vulns = raw_vulns

    issues = [
        Issue(
            id=str(i.get("number", "")),
            title=i.get("standardized_title") or i.get("title", ""),
            state=i.get("state", "open"),
            created_at=i.get("created_at"),
            body=(i.get("body") or "")[:1000] or None,
            type=i.get("type"),
            summary=i.get("summary"),
            updated_at=i.get("updated_at"),
            closed_at=i.get("closed_at"),
            labels=[
                lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
                for lbl in (i.get("labels") or [])
            ],
        )
        for i in curated_issues
        if i.get("number") is not None
    ]

    releases = [
        Release(
            tag=r.get("tag_name") or str(r.get("id", "")),
            name=r.get("standardized_title") or r.get("name"),
            published_at=r.get("published_at"),
            body=(r.get("body") or "")[:2000] or None,
            release_type=r.get("release_type"),
            change_summary=r.get("change_summary"),
        )
        for r in curated_releases
    ]

    vulnerabilities = [
        Vulnerability(
            id=v.get("ghsa_id", ""),
            severity=v.get("severity_category", "unknown"),
            description=v.get("summary"),
            cve_id=v.get("cve_id"),
            affected_components=v.get("affected_components") or [],
            published_at=v.get("published_at"),
            cvss_score=_safe_float(
                (v.get("cvss") or {}).get("score")
                if isinstance(v.get("cvss"), dict)
                else v.get("cvss_score")
            ),
            cwe_ids=[
                c.get("cwe_id", "") if isinstance(c, dict) else str(c)
                for c in (v.get("cwes") or [])
            ],
        )
        for v in curated_vulns
        if v.get("ghsa_id")
    ]

    repository = Repository(
        url=url,
        owner=owner,
        name=name,
        issues=issues,
        releases=releases,
        vulnerabilities=vulnerabilities,
    )
    entry = RepoEntry(repositories=[repository])

    try:
        await repo_cache_dao.upsert_cached_entry(
            RepoCacheEntry(
                owner=owner,
                repo_name=name,
                lookback_days=settings.lookback_days,
                fetched_at=datetime.now(UTC),
                entry=entry,
            )
        )
    except Exception:
        _log.warning("repo.analyze: cache write failed for %s/%s", owner, name)

    result_id = await repo_dao.save(entry)
    _log.info(
        "repo.analyze: saved — issues=%d releases=%d vulns=%d result_id=%s",
        len(issues),
        len(releases),
        len(vulnerabilities),
        result_id,
    )
    return {"result_id": result_id}
```

- [ ] **Step 4: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/graph.py`**

```python
"""Repo subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.repo.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.repo.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState

GRAPH_NAME = "repo"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Fetches GitHub signals for one dependency via the workers service:"
        " issues, releases, and security advisories, then curates them with LLM agents"
    )


def build_repo_subgraph() -> StateGraph:
    builder = StateGraph(RepoState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


repo_subgraph = build_repo_subgraph()
```

- [ ] **Step 5: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import repo_dao
from src.main_graph.subgraphs.ingestion_subgraphs.repo.graph import (
    DEPENDS_ON,
    GRAPH_NAME,
    build_repo_subgraph,
    describe,
    repo_subgraph,
)
from src.main_graph.subgraphs.ingestion_subgraphs.repo.state import RepoState

subgraph = repo_subgraph

__all__ = [
    "DEPENDS_ON",
    "GRAPH_NAME",
    "build_repo_subgraph",
    "describe",
    "repo_dao",
    "repo_subgraph",
    "subgraph",
    "RepoState",
]
```

- [ ] **Step 6: Verify import**

```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs import repo; print(repo.GRAPH_NAME)"
```
Expected: `repo`

- [ ] **Step 7: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/repo/
git commit -m "feat: rework repo subgraph — workers integration, drop commits, add graph.py"
```

---

### Task 6: Register All Subgraphs

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`

- [ ] **Step 1: Update `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs import (
    license_compliance,
    registry,
    repo,
    runtime,
    vulnerabilities,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.registry.dao import registry_dao
from src.main_graph.subgraphs.ingestion_subgraphs.repo.dao import repo_dao
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.dao import runtime_dao
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)

_MODULES = [vulnerabilities, license_compliance, registry, repo, runtime]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}
SUBGRAPH_DAOS = {
    "vulnerabilities": vulnerabilities_dao,
    "license_compliance": license_compliance_dao,
    "registry": registry_dao,
    "repo": repo_dao,
    "runtime": runtime_dao,
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
    "SUBGRAPH_DAOS",
]
```

- [ ] **Step 2: Verify all subgraphs import correctly**

```bash
cd apps/backend && uv run python -c "
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY
print(list(SUBGRAPH_REGISTRY.keys()))
"
```
Expected: `['vulnerabilities', 'license_compliance', 'registry', 'repo', 'runtime']`

- [ ] **Step 3: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py
git commit -m "feat: register registry, repo, runtime in SUBGRAPH_REGISTRY"
```

---

### Task 7: Update Planner and Orchestrator for Plan Object

**Files:**
- Modify: `apps/backend/src/main_graph/nodes/planner.py`
- Modify: `apps/backend/src/main_graph/nodes/orchestrator.py`
- Modify: `tests/unit/nodes/test_planner.py`

The planner now returns a `Plan` TypedDict. The orchestrator's `_present_plan` and `_classify_intent` helpers are updated accordingly.

- [ ] **Step 1: Update failing tests for planner**

Replace `tests/unit/nodes/test_planner.py` with:

```python
# tests/unit/nodes/test_planner.py
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.planner import _FALLBACK_PLAN, run_planner


def _make_state(
    components: list[dict], concern: str = "security", summary: str = "ok"
) -> dict:
    return {
        "job_id": "j1",
        "concern": concern,
        "discovery_summary": summary,
        "sbom_cyclonedx": {"components": components},
        "repo_url": "http://example.com/repo",
        "messages": [],
        "subgraph_results": [],
    }


@pytest.mark.asyncio
async def test_planner_returns_plan_object():
    components = [{"name": "requests"}, {"name": "flask"}]
    state = _make_state(components)

    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None}
    )

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert plan["subgraphs"] == ["vulnerabilities", "registry"]
    assert plan["dep_filter"] is None


@pytest.mark.asyncio
async def test_planner_accepts_legacy_list_response():
    """LLM returning a plain list is accepted and wrapped in Plan."""
    state = _make_state([{"name": "lodash"}])

    mock_response = MagicMock()
    mock_response.content = json.dumps(["vulnerabilities"])

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert plan["subgraphs"] == ["vulnerabilities"]


@pytest.mark.asyncio
async def test_planner_falls_back_on_bad_json():
    state = _make_state([{"name": "lodash"}])

    mock_response = MagicMock()
    mock_response.content = "not json at all"

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert plan == _FALLBACK_PLAN


@pytest.mark.asyncio
async def test_planner_includes_dep_filter():
    state = _make_state([{"name": "axios"}])

    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {"subgraphs": ["registry", "repo"], "dep_filter": ["axios"]}
    )

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state, extra_instructions="only check axios")

    assert plan["dep_filter"] == ["axios"]
```

- [ ] **Step 2: Run to verify failure**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_planner.py -v
```
Expected: failures because `run_planner` returns `list[str]`, not `Plan`

- [ ] **Step 3: Replace `apps/backend/src/main_graph/nodes/planner.py`**

```python
"""Planner — selects analysis subgraphs and optional dep_filter based on concern."""

import json
import logging

from src.main_graph.plan import Plan
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DESCRIPTIONS,
    SUBGRAPH_REGISTRY,
)
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_PIPELINE_SUBGRAPHS: list[tuple[str, str]] = [
    ("risk_score", "Computes a composite 0–10 risk score per dependency from all analysis signals"),
    ("recommendation", "Finds 1–3 maintained alternatives for each high-risk dependency"),
]

VALID_SUBGRAPHS: set[str] = set(SUBGRAPH_REGISTRY.keys()) | {
    name for name, _ in _PIPELINE_SUBGRAPHS
}

_FALLBACK_PLAN: Plan = {"subgraphs": ["vulnerabilities", "risk_score", "recommendation"], "dep_filter": None}

_ingestion_lines = "\n".join(
    f"- {name}: {desc}"
    for entry in SUBGRAPH_DESCRIPTIONS
    for name, desc in [entry.split(":", 1)]
)
_pipeline_lines = "\n".join(f"- {name}: {desc}" for name, desc in _PIPELINE_SUBGRAPHS)
_example = json.dumps({"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None})
_example_filter = json.dumps({"subgraphs": ["registry", "repo", "runtime"], "dep_filter": ["react", "lodash"]})

_SYSTEM_PROMPT = f"""\
You are a dependency analysis planner. Given a project's dependency discovery
summary, its components, and a user concern, decide which analysis subgraphs
to run. Available subgraphs:

{_ingestion_lines}
{_pipeline_lines}

Return ONLY a valid JSON object with keys:
  "subgraphs": array of subgraph names relevant to the user's concern
  "dep_filter": array of specific package names to focus on, or null for all dependencies

Examples:
  Security concern: {_example}
  Specific dep concern: {_example_filter}

risk_score and recommendation always run and do not need to be included in subgraphs.
If additional instructions are provided, honor them — they reflect updated user preferences.
"""


async def run_planner(state: MainState, extra_instructions: str = "") -> Plan:
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    sbom = state.get("sbom_cyclonedx", {})

    components = sbom.get("components", [])
    comp_list = ", ".join(c["name"] for c in components[:30])
    if len(components) > 30:
        comp_list += f", and {len(components) - 30} more"

    user_message = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Components ({len(components)}): {comp_list}"
    )
    if extra_instructions:
        user_message += f"\n\nAdditional instructions from the user: {extra_instructions}"

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    )

    try:
        parsed = parse_llm_json(response.content or "")
        if isinstance(parsed, list):
            # Accept legacy list format
            subgraphs = [s for s in parsed if s in VALID_SUBGRAPHS]
            plan: Plan = {"subgraphs": subgraphs or _FALLBACK_PLAN["subgraphs"], "dep_filter": None}
        elif isinstance(parsed, dict):
            subgraphs = [s for s in (parsed.get("subgraphs") or []) if s in VALID_SUBGRAPHS]
            plan = {"subgraphs": subgraphs or _FALLBACK_PLAN["subgraphs"], "dep_filter": parsed.get("dep_filter")}
        else:
            plan = _FALLBACK_PLAN
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("run_planner: failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("run_planner: selected subgraphs=%s dep_filter=%s", plan["subgraphs"], plan.get("dep_filter"))
    return plan
```

- [ ] **Step 4: Update orchestrator to handle Plan object**

In `apps/backend/src/main_graph/nodes/orchestrator.py`, update `_present_plan` and `_classify_intent` to work with `Plan`:

```python
# Replace the _present_plan function:
def _present_plan(plan: Plan) -> str:
    """Build a deterministic presentation of the plan."""
    subgraphs = plan.get("subgraphs", []) if isinstance(plan, dict) else list(plan)
    dep_filter = plan.get("dep_filter") if isinstance(plan, dict) else None
    lines = ["**Proposed Analysis Plan:**\n"]
    for i, name in enumerate(subgraphs, 1):
        desc = _SUBGRAPH_DESC.get(name, name)
        lines.append(f"{i}. **{name}**: {desc}")
    if dep_filter:
        lines.append(f"\n**Scope:** {', '.join(dep_filter)}")
    lines.append(
        "\nWould you like to proceed with this plan, request changes, or cancel?"
    )
    return "\n".join(lines)
```

Also update `_classify_intent` to extract the subgraph list from Plan:

```python
# Replace _classify_intent body:
async def _classify_intent(plan: Plan, user_input: str) -> str:
    subgraphs = plan.get("subgraphs", []) if isinstance(plan, dict) else list(plan)
    plan_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(subgraphs))
    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Plan:\n{plan_str}\n\nUser message: {user_input}",
            },
        ]
    )
    intent = response.content.strip().lower()
    if intent not in ("approve", "change", "cancel"):
        logger.warning(
            "orchestrator: unexpected intent %r, defaulting to 'change'", intent
        )
        intent = "change"
    return intent
```

Also update the `orchestrator` function type hint: `plan: Plan` and the call to `_present_plan(plan)`.

Add the import at the top of orchestrator.py:
```python
from src.main_graph.plan import Plan
```

And update `_PIPELINE_SUBGRAPHS` import to reference it directly:
```python
from src.main_graph.nodes.planner import _PIPELINE_SUBGRAPHS, run_planner
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_planner.py -v
```
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/nodes/planner.py \
        apps/backend/src/main_graph/nodes/orchestrator.py \
        apps/backend/tests/unit/nodes/test_planner.py
git commit -m "feat: planner returns Plan object with subgraphs + dep_filter"
```

---

### Task 8: Update Execution Model — Per-Dep Fan-Out

**Files:**
- Modify: `apps/backend/src/main_graph/nodes/execution_planner.py`
- Modify: `apps/backend/src/main_graph/nodes/task_dispatcher.py`
- Modify: `apps/backend/src/main_graph/nodes/execute_plan.py`
- Create: `tests/unit/nodes/test_execution_planner.py`

The `execution_stages` type changes from `list[list[str]]` to `list[list[dict]]`.
Each dict: `{"subgraph": str, "dep_name": str | None}`.

- [ ] **Step 1: Write failing execution_planner tests**

```python
# tests/unit/nodes/test_execution_planner.py
import pytest

from src.main_graph.nodes.execution_planner import execution_planner

_SBOM = {
    "components": [
        {"name": "lodash", "version": "4.17.21"},
        {"name": "express", "version": "4.18.0"},
    ]
}


def _make_state(**kwargs) -> dict:
    base = {
        "job_id": "j1",
        "concern": "security",
        "sbom_cyclonedx": _SBOM,
        "plan": {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None},
        "messages": [],
        "subgraph_results": [],
    }
    base.update(kwargs)
    return base


def test_execution_planner_generates_sbom_level_entry():
    state = _make_state()
    result = execution_planner(state)

    stage0 = result["execution_stages"][0]
    sbom_entries = [e for e in stage0 if e["subgraph"] == "vulnerabilities"]
    assert len(sbom_entries) == 1
    assert sbom_entries[0]["dep_name"] is None


def test_execution_planner_generates_per_dep_entries():
    state = _make_state()
    result = execution_planner(state)

    stage0 = result["execution_stages"][0]
    registry_entries = [e for e in stage0 if e["subgraph"] == "registry"]
    dep_names = {e["dep_name"] for e in registry_entries}
    assert dep_names == {"lodash", "express"}


def test_execution_planner_respects_dep_filter():
    state = _make_state(plan={"subgraphs": ["registry"], "dep_filter": ["lodash"]})
    result = execution_planner(state)

    stage0 = result["execution_stages"][0]
    registry_entries = [e for e in stage0 if e["subgraph"] == "registry"]
    assert len(registry_entries) == 1
    assert registry_entries[0]["dep_name"] == "lodash"


def test_execution_planner_skips_if_stages_exist():
    state = _make_state(execution_stages=[[{"subgraph": "vulnerabilities", "dep_name": None}]])
    result = execution_planner(state)
    assert result == {}


def test_execution_planner_sets_stage_index_zero():
    state = _make_state()
    result = execution_planner(state)
    assert result["current_stage_index"] == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_execution_planner.py -v
```
Expected: failures due to current flat-list implementation

- [ ] **Step 3: Replace `apps/backend/src/main_graph/nodes/execution_planner.py`**

```python
"""Execution planner node — builds per-dep fan-out Stage 1 entries once."""

from src.main_graph.state import MainState

_SBOM_LEVEL = frozenset({"vulnerabilities", "license_compliance"})
_PER_DEP = frozenset({"registry", "repo", "runtime"})


def execution_planner(state: MainState) -> dict:
    """Compute Stage 1 execution entries. Runs once; subsequent calls are no-ops."""
    if state.get("execution_stages") is not None:
        return {}

    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else list(plan_obj)
    )
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )

    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    stage1: list[dict] = []

    for sg in subgraphs:
        if sg in _SBOM_LEVEL:
            stage1.append({"subgraph": sg, "dep_name": None})

    for sg in subgraphs:
        if sg in _PER_DEP:
            for dep in dep_scope:
                stage1.append({"subgraph": sg, "dep_name": dep})

    return {
        "execution_stages": [stage1],
        "current_stage_index": 0,
        "dep_filter": dep_filter,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_execution_planner.py -v
```
Expected: 5 tests PASS

- [ ] **Step 5: Update `apps/backend/src/main_graph/nodes/task_dispatcher.py`**

```python
"""Task dispatcher — fans out the current stage via Send."""

from typing import Any

from langgraph.types import Send

from src.main_graph.constants import EXECUTE_PLAN
from src.main_graph.state import MainState


def task_dispatcher(state: MainState) -> list[Send]:
    """Return one Send per entry in the current execution stage."""
    stages = state.get("execution_stages", [])
    idx = state.get("current_stage_index", 0)
    current_stage: list[dict] = stages[idx] if idx < len(stages) else []

    upstream_results: dict[str, Any] = {
        entry["subgraph"]: entry.get("result_id")
        for entry in state.get("subgraph_results", [])
        if entry.get("result_id")
    }

    return [
        Send(
            EXECUTE_PLAN,
            {
                "subgraph_name": entry["subgraph"],
                "dep_name": entry.get("dep_name"),
                "job_id": state.get("job_id", ""),
                "discovery_summary": state.get("discovery_summary", ""),
                "concern": state.get("concern", ""),
                "sbom_cyclonedx": state.get("sbom_cyclonedx", {}),
                "repo_path": state.get("repo_path"),
                "upstream_results": upstream_results,
                "subgraph_results": [],
            },
        )
        for entry in current_stage
    ]
```

- [ ] **Step 6: Update `apps/backend/src/main_graph/nodes/execute_plan.py`**

Key changes:
- Read `dep_name` from state
- Pass `dep_name` (as `dependency_name`) in the subgraph invocation
- Include `dep_name` in the result dict for downstream aggregation
- Fix the `outputdao` typo on line ~39

```python
"""Execute plan node — dispatches to the appropriate subgraph by name."""

import logging

from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DAOS,
    SUBGRAPH_REGISTRY,
)
from src.services.dependencies import get_job_repo

logger = logging.getLogger(__name__)


async def execute_plan(state: MainState) -> dict:
    dao = get_job_repo()
    name = state.get("subgraph_name", "")
    dep_name: str | None = state.get("dep_name")
    job_id = state.get("job_id", "")

    subgraph = SUBGRAPH_REGISTRY.get(name)

    if subgraph is None:
        logger.warning("execute_plan: unknown subgraph %r", name)
        if job_id:
            await dao.complete_artifact(job_id, name, "failed")
        return {"subgraph_results": [{"subgraph": name, "dep_name": dep_name, "error": "unknown subgraph"}]}

    artifact_key = f"{name}:{dep_name}" if dep_name else name
    if job_id:
        await dao.start_artifact(job_id, artifact_key)

    try:
        hydrated_upstream = {}
        for sg, result_id in state.get("upstream_results", {}).items():
            output_dao = SUBGRAPH_DAOS.get(sg)
            if output_dao and result_id:
                data = await output_dao.get(result_id)
                if data:
                    hydrated_upstream[sg] = data

        invocation: dict = {
            "sbom_cyclonedx": state.get("sbom_cyclonedx", {}),
            "discovery_summary": state.get("discovery_summary", ""),
            "concern": state.get("concern", ""),
            "upstream_results": hydrated_upstream,
        }
        if repo_path := state.get("repo_path"):
            invocation["repo_path"] = repo_path
        if dep_name:
            invocation["dependency_name"] = dep_name

        result = await subgraph.ainvoke(invocation)

        result_id = result.get("result_id")
        if job_id:
            await dao.update_artifact_data(job_id, artifact_key, {"result_id": result_id})
            await dao.complete_artifact(job_id, artifact_key, "done")
        logger.info("execute_plan: %s(%s) completed, result_id=%s", name, dep_name, result_id)
        return {"subgraph_results": [{"subgraph": name, "dep_name": dep_name, "result_id": result_id}]}
    except Exception:
        logger.exception("execute_plan: %s(%s) failed", name, dep_name)
        if job_id:
            await dao.complete_artifact(job_id, artifact_key, "failed")
        raise
```

- [ ] **Step 7: Run all unit tests to verify nothing is broken**

```bash
cd apps/backend && uv run pytest tests/unit/ -v
```
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add apps/backend/src/main_graph/nodes/execution_planner.py \
        apps/backend/src/main_graph/nodes/task_dispatcher.py \
        apps/backend/src/main_graph/nodes/execute_plan.py \
        apps/backend/tests/unit/nodes/test_execution_planner.py
git commit -m "feat: per-dep fan-out execution model — Plan object, dict stages, dep_name in Send"
```

---

### Task 9: risk_ranker Stub + Stage Routing

**Files:**
- Create: `apps/backend/src/main_graph/nodes/risk_ranker.py`
- Modify: `apps/backend/src/main_graph/nodes/stage_advance.py`
- Modify: `apps/backend/src/main_graph/constants.py`

The stub `risk_ranker` selects the first 3 deps in scope as `high_risk_deps` and — if `impact` is in the plan — appends Stage 2 entries to `execution_stages`. The `stage_router` gains two extra routes: `RISK_RANKER` (after Stage 1) and `RISK_SCORE` (after Stage 2).

- [ ] **Step 1: Update `apps/backend/src/main_graph/constants.py`**

```python
DISCOVERY = "discovery"
ORCHESTRATOR = "orchestrator"
EXECUTION_PLANNER = "execution_planner"
EXECUTE_PLAN = "execute_plan"
STAGE_ADVANCE = "stage_advance"
RISK_RANKER = "risk_ranker"
RISK_SCORE = "risk_score"
RECOMMENDATION = "recommendation"
CROSS_ANALYZER = "cross_analyzer"
REPORT_REVIEWER = "report_reviewer"
```

- [ ] **Step 2: Create `apps/backend/src/main_graph/nodes/risk_ranker.py`**

```python
"""risk_ranker — stub agentic node and stage router.

Stub behaviour: selects the first 3 deps in scope as high_risk_deps.
Full implementation follows the stage-3-synthesis spec.
"""

import logging

from src.main_graph.constants import EXECUTION_PLANNER, RISK_SCORE
from src.main_graph.state import MainState

_log = logging.getLogger(__name__)


async def risk_ranker(state: MainState) -> dict:
    """Select high-risk deps and optionally extend execution_stages with Stage 2."""
    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else []
    )
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )

    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    # Stub: select first 3 deps as high-risk
    high_risk_deps = dep_scope[:3]
    _log.info("risk_ranker(stub): high_risk_deps=%s", high_risk_deps)

    risk_rankings = [
        {
            "dep_name": dep,
            "preliminary_score": 5.0,
            "risk_signals": [],
            "rationale": "stub — full analysis pending",
        }
        for dep in dep_scope
    ]

    existing_stages = state.get("execution_stages") or []
    new_stages = list(existing_stages)

    if "impact" in subgraphs and high_risk_deps:
        stage2 = [{"subgraph": "impact", "dep_name": dep} for dep in high_risk_deps]
        new_stages = existing_stages + [stage2]

    return {
        "execution_stages": new_stages,
        "risk_rankings": risk_rankings,
        "high_risk_deps": high_risk_deps,
        "risk_ranker_done": True,
    }


def risk_ranker_router(state: MainState) -> str:
    """Route after risk_ranker: to Stage 2 dispatch or directly to risk_score."""
    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else []
    )
    if "impact" in subgraphs and state.get("high_risk_deps"):
        return EXECUTION_PLANNER
    return RISK_SCORE
```

- [ ] **Step 3: Update `apps/backend/src/main_graph/nodes/stage_advance.py`**

```python
"""Stage advance node and router — move to the next execution stage."""

from src.main_graph.constants import CROSS_ANALYZER, EXECUTION_PLANNER, RISK_RANKER, RISK_SCORE
from src.main_graph.state import MainState


def stage_advance(state: MainState) -> dict:
    """Increment the stage counter after a parallel batch completes."""
    return {"current_stage_index": state.get("current_stage_index", 0) + 1}


def stage_router(state: MainState) -> str:
    """Route after a stage completes."""
    idx = state.get("current_stage_index", 0)
    stages = state.get("execution_stages", [])

    if idx < len(stages):
        return EXECUTION_PLANNER

    if not state.get("risk_ranker_done"):
        return RISK_RANKER

    return RISK_SCORE
```

- [ ] **Step 4: Verify import**

```bash
cd apps/backend && uv run python -c "from src.main_graph.nodes.risk_ranker import risk_ranker, risk_ranker_router; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/constants.py \
        apps/backend/src/main_graph/nodes/risk_ranker.py \
        apps/backend/src/main_graph/nodes/stage_advance.py
git commit -m "feat: risk_ranker stub, update stage_router for RISK_RANKER/RISK_SCORE routing"
```

---

### Task 10: Impact Subgraph Stub

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/` (all files)

The stub saves an empty `ImpactEntry` and returns a `result_id`. Full agentic implementation follows `2026-05-17-stage2-impact-design.md`.

- [ ] **Step 1: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/models.py`**

```python
from pydantic import BaseModel, Field


class ImpactEntry(BaseModel):
    dep_name: str = ""
    usage_count: int = 0
    affected_files: list[str] = Field(default_factory=list)
    api_surface_used: list[str] = Field(default_factory=list)
    usage_summary: str = ""
    direct_dependents: int = 0
    transitive_dependents: int = 0
    max_depth: int = 0
    blast_radius_summary: str = ""
```

- [ ] **Step 2: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/state.py`**

```python
from typing import NotRequired

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class ImpactState(AnalysisState):
    result_id: NotRequired[str]
```

- [ ] **Step 3: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/dao.py`**

```python
from bson import ObjectId

from src.db.connection import get_db
from src.main_graph.subgraphs.ingestion_subgraphs.impact.models import ImpactEntry


class ImpactDAO:
    @property
    def _col(self):
        return get_db()["impact_results"]

    async def save(self, entry: ImpactEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


impact_dao = ImpactDAO()
```

- [ ] **Step 4: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/constants.py`**

```python
ANALYZE = "analyze"
```

- [ ] **Step 5: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/nodes/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze import analyze

__all__ = ["analyze"]
```

- [ ] **Step 6: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/nodes/analyze.py`**

```python
"""Impact analysis node — stub.

Full agentic implementation specified in 2026-05-17-stage2-impact-design.md.
"""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import impact_dao
from src.main_graph.subgraphs.ingestion_subgraphs.impact.models import ImpactEntry
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState

_log = logging.getLogger(__name__)


async def analyze(state: ImpactState) -> dict:
    dep_name = state.get("dependency_name", "")
    _log.info("impact.analyze(stub): dep=%s", dep_name)
    entry = ImpactEntry(dep_name=dep_name)
    result_id = await impact_dao.save(entry)
    return {"result_id": result_id}
```

- [ ] **Step 7: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/graph.py`**

```python
"""Impact subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.impact.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState

GRAPH_NAME = "impact"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Analyzes how one high-risk dependency is used in the user's project:"
        " static import scan and transitive blast radius from the SBOM dependency tree"
    )


def build_impact_subgraph() -> StateGraph:
    builder = StateGraph(ImpactState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


impact_subgraph = build_impact_subgraph()
```

- [ ] **Step 8: Create `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/__init__.py`**

```python
from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import impact_dao
from src.main_graph.subgraphs.ingestion_subgraphs.impact.graph import (
    DEPENDS_ON,
    GRAPH_NAME,
    build_impact_subgraph,
    describe,
    impact_subgraph,
)
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState

subgraph = impact_subgraph

__all__ = [
    "DEPENDS_ON",
    "GRAPH_NAME",
    "build_impact_subgraph",
    "describe",
    "impact_dao",
    "impact_subgraph",
    "subgraph",
    "ImpactState",
]
```

- [ ] **Step 9: Register impact in ingestion subgraphs `__init__.py`**

In `apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`, add `impact` to the imports and `_MODULES`:

```python
from src.main_graph.subgraphs.ingestion_subgraphs import (
    impact,
    license_compliance,
    registry,
    repo,
    runtime,
    vulnerabilities,
)
from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import impact_dao

# Add to _MODULES:
_MODULES = [vulnerabilities, license_compliance, registry, repo, runtime, impact]

# Add to SUBGRAPH_DAOS:
SUBGRAPH_DAOS = {
    "vulnerabilities": vulnerabilities_dao,
    "license_compliance": license_compliance_dao,
    "registry": registry_dao,
    "repo": repo_dao,
    "runtime": runtime_dao,
    "impact": impact_dao,
}
```

- [ ] **Step 10: Verify**

```bash
cd apps/backend && uv run python -c "
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY
print(list(SUBGRAPH_REGISTRY.keys()))
"
```
Expected: `['vulnerabilities', 'license_compliance', 'registry', 'repo', 'runtime', 'impact']`

- [ ] **Step 11: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/impact/ \
        apps/backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py
git commit -m "feat: impact subgraph stub — correct interfaces, empty results"
```

---

### Task 11: risk_score + recommendation Stubs + Graph Topology

**Files:**
- Create: `apps/backend/src/main_graph/nodes/risk_score.py`
- Create: `apps/backend/src/main_graph/nodes/recommendation.py`
- Modify: `apps/backend/src/main_graph/nodes/__init__.py`
- Modify: `apps/backend/src/main_graph/graph.py`
- Modify: `apps/backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py`

- [ ] **Step 1: Create `apps/backend/src/main_graph/nodes/risk_score.py`**

```python
"""risk_score node — stub.

Full agentic implementation specified in 2026-05-17-stage3-synthesis-design.md.
"""

import logging

from src.main_graph.state import MainState

_log = logging.getLogger(__name__)


async def risk_score(state: MainState) -> dict:
    """Compute final risk scores per dep — stub assigns 5.0 to all deps."""
    plan_obj = state.get("plan") or {}
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )
    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    scores = [
        {
            "dep_name": dep,
            "score": 5.0,
            "severity": "medium",
            "breakdown": {},
            "rationale": "stub — full analysis pending",
            "impact_weight": None,
        }
        for dep in dep_scope
    ]
    _log.info("risk_score(stub): scored %d deps", len(scores))
    return {"risk_scores": scores}
```

- [ ] **Step 2: Create `apps/backend/src/main_graph/nodes/recommendation.py`**

```python
"""recommendation node — stub.

Full agentic implementation specified in 2026-05-17-stage3-synthesis-design.md.
"""

import logging

from src.main_graph.state import MainState

_log = logging.getLogger(__name__)


async def recommendation(state: MainState) -> dict:
    """Generate alternatives for high-risk deps — stub returns empty recommendations."""
    high_risk_deps = state.get("high_risk_deps") or []
    recs = [
        {
            "dep_name": dep,
            "risk_summary": "stub — full analysis pending",
            "alternatives": [],
            "migration_notes": "",
        }
        for dep in high_risk_deps
    ]
    _log.info("recommendation(stub): %d deps", len(recs))
    return {"recommendations": recs}
```

- [ ] **Step 3: Update `apps/backend/src/main_graph/nodes/__init__.py`**

```python
from src.main_graph.nodes.execute_plan import execute_plan
from src.main_graph.nodes.execution_planner import execution_planner
from src.main_graph.nodes.orchestrator import orchestrator
from src.main_graph.nodes.recommendation import recommendation
from src.main_graph.nodes.risk_ranker import risk_ranker, risk_ranker_router
from src.main_graph.nodes.risk_score import risk_score
from src.main_graph.nodes.stage_advance import stage_advance, stage_router
from src.main_graph.nodes.task_dispatcher import task_dispatcher

__all__ = [
    "execute_plan",
    "execution_planner",
    "orchestrator",
    "recommendation",
    "risk_ranker",
    "risk_ranker_router",
    "risk_score",
    "stage_advance",
    "stage_router",
    "task_dispatcher",
]
```

- [ ] **Step 4: Update `apps/backend/src/main_graph/graph.py`**

```python
"""Main graph — composes all subgraphs into the full analysis pipeline."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    CROSS_ANALYZER,
    DISCOVERY,
    EXECUTE_PLAN,
    EXECUTION_PLANNER,
    ORCHESTRATOR,
    RECOMMENDATION,
    REPORT_REVIEWER,
    RISK_RANKER,
    RISK_SCORE,
    STAGE_ADVANCE,
)
from src.main_graph.nodes import (
    execute_plan,
    execution_planner,
    orchestrator,
    recommendation,
    risk_ranker,
    risk_ranker_router,
    risk_score,
    stage_advance,
    stage_router,
    task_dispatcher,
)
from src.main_graph.state import MainState
from src.main_graph.subgraphs import (
    cross_analyzer_subgraph,
    discovery_subgraph,
    report_reviewer_subgraph,
)

_checkpointer = InMemorySaver()

_MAX_REVIEW_ITERATIONS = 2


def _review_router(state: MainState) -> str:
    if (
        state.get("review_approved")
        or state.get("review_iterations", 0) >= _MAX_REVIEW_ITERATIONS
    ):
        return END
    return CROSS_ANALYZER


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(DISCOVERY, discovery_subgraph)
    builder.add_node(ORCHESTRATOR, orchestrator)
    builder.add_node(EXECUTION_PLANNER, execution_planner)
    builder.add_node(EXECUTE_PLAN, execute_plan)
    builder.add_node(STAGE_ADVANCE, stage_advance)
    builder.add_node(RISK_RANKER, risk_ranker)
    builder.add_node(RISK_SCORE, risk_score)
    builder.add_node(RECOMMENDATION, recommendation)
    builder.add_node(CROSS_ANALYZER, cross_analyzer_subgraph)
    builder.add_node(REPORT_REVIEWER, report_reviewer_subgraph)

    builder.add_edge(START, DISCOVERY)
    builder.add_edge(DISCOVERY, ORCHESTRATOR)
    builder.add_edge(ORCHESTRATOR, EXECUTION_PLANNER)
    builder.add_conditional_edges(EXECUTION_PLANNER, task_dispatcher, [EXECUTE_PLAN])
    builder.add_edge(EXECUTE_PLAN, STAGE_ADVANCE)
    builder.add_conditional_edges(
        STAGE_ADVANCE, stage_router, [EXECUTION_PLANNER, RISK_RANKER, RISK_SCORE]
    )
    builder.add_conditional_edges(
        RISK_RANKER, risk_ranker_router, [EXECUTION_PLANNER, RISK_SCORE]
    )
    builder.add_edge(RISK_SCORE, RECOMMENDATION)
    builder.add_edge(RECOMMENDATION, CROSS_ANALYZER)
    builder.add_edge(CROSS_ANALYZER, REPORT_REVIEWER)
    builder.add_conditional_edges(
        REPORT_REVIEWER, _review_router, [CROSS_ANALYZER, END]
    )

    return builder.compile(checkpointer=_checkpointer)


main_graph = build_main_graph()
```

- [ ] **Step 5: Update `apps/backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py`**

```python
"""Cross-analyzer node — assembles the unified structured report from Stage 3 artifacts."""

import logging
from datetime import UTC, datetime

from src.main_graph.subgraphs.cross_analyzer.state import CrossAnalyzerState

logger = logging.getLogger(__name__)


async def analyze(state: CrossAnalyzerState) -> dict:
    concern = state.get("concern", "")
    feedback = state.get("reviewer_feedback")
    iteration = state.get("review_iterations", 0)

    if feedback:
        logger.info(
            "cross_analyzer: rebuilding report on iteration %d — feedback: %s",
            iteration,
            feedback,
        )

    risk_scores = state.get("risk_scores") or []
    recommendations = state.get("recommendations") or []
    risk_rankings = state.get("risk_rankings") or []

    by_dep: dict[str, dict] = {}
    for entry in risk_scores:
        dep = entry.get("dep_name", "")
        by_dep.setdefault(dep, {})["risk_score"] = entry

    for entry in recommendations:
        dep = entry.get("dep_name", "")
        by_dep.setdefault(dep, {})["recommendation"] = entry

    for entry in risk_rankings:
        dep = entry.get("dep_name", "")
        by_dep.setdefault(dep, {})["ranking"] = entry

    report: dict = {
        "concern": concern,
        "generated_at": datetime.now(UTC).isoformat(),
        "iteration": iteration + 1,
        "dependencies": by_dep,
        "reviewer_notes": feedback or None,
    }

    logger.info("cross_analyzer: report built, deps=%d", len(by_dep))
    return {
        "analysis_report": report,
        "review_iterations": iteration + 1,
        "reviewer_feedback": None,
    }
```

- [ ] **Step 6: Verify the graph compiles**

```bash
cd apps/backend && uv run python -c "from src.main_graph.graph import main_graph; print('graph ok')"
```
Expected: `graph ok`

- [ ] **Step 7: Run all unit tests**

```bash
cd apps/backend && uv run pytest tests/unit/ -v
```
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add apps/backend/src/main_graph/nodes/risk_score.py \
        apps/backend/src/main_graph/nodes/recommendation.py \
        apps/backend/src/main_graph/nodes/__init__.py \
        apps/backend/src/main_graph/graph.py \
        apps/backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py
git commit -m "feat: complete pipeline topology — risk_score/recommendation stubs, updated graph + cross_analyzer"
```

---

## Self-Review Checklist

After writing the plan:

**Spec Coverage:**
- [x] `dependency_name` added to `AnalysisState` — Task 1
- [x] `Plan` TypedDict (subgraphs + dep_filter) — Task 1
- [x] `MainState` updated with new fields — Task 1
- [x] Workers client (POST /ingest + poll) — Task 2
- [x] SBOM VCS URL extraction (get_vcs_url, parse_github_owner_repo) — Task 3
- [x] `runtime` updated to use dependency_name + SBOM (drop direct_dependencies) — Task 3
- [x] `registry` new subgraph with workers integration — Task 4
- [x] `repo` reworked: workers + SBOM, commits dropped, graph.py added — Task 5
- [x] All subgraphs registered — Tasks 6 + 10 (impact)
- [x] `planner.py` produces Plan object — Task 7
- [x] `orchestrator.py` handles Plan object — Task 7
- [x] `execution_planner` per-dep fan-out — Task 8
- [x] `task_dispatcher` reads dep_name from entry dict — Task 8
- [x] `execute_plan` passes dep_name as dependency_name + fixes typo — Task 8
- [x] `risk_ranker` stub between Stage 1 and Stage 2 — Task 9
- [x] `stage_router` routes to RISK_RANKER then RISK_SCORE — Task 9
- [x] `impact` subgraph stub (correct interfaces) — Task 10
- [x] `risk_score` stub — Task 11
- [x] `recommendation` stub — Task 11
- [x] Main graph topology updated — Task 11
- [x] `cross_analyzer` narrows to Stage 3 aggregation — Task 11

**Type consistency:**
- `Plan` TypedDict used consistently in planner, orchestrator, execution_planner, risk_ranker, risk_score, recommendation
- `execution_stages: list[list[dict]]` — each dict `{"subgraph": str, "dep_name": str | None}` used in execution_planner, task_dispatcher, risk_ranker
- `subgraph_results` entries now include `dep_name` field — execute_plan writes it, risk_ranker reads it

**No placeholders:** All steps contain complete code. Workers cache document field names (`items`, `data`) may need adjustment after verifying the actual workers adapter schema — noted inline in registry/analyze.py and repo/analyze.py.
