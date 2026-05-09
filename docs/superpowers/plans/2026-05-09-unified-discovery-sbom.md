# Unified Discovery + SBOM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge Trivy (CycloneDX-only) into the discovery subgraph, replace manual dep lists with `sbom_cyclonedx` throughout, and delete the standalone `sbom_gen` ingestion subgraph.

**Architecture:** `fetch_repository` clones only → `generate_sbom` runs Trivy CycloneDX + saves to MongoDB → `build_dependency_summary` reads CycloneDX for metadata + LLM summary. All state fields that previously held `direct_dependencies`/`transitive_dependencies`/`dependency_tree` are replaced by `sbom_cyclonedx`. Downstream subgraphs (`vulnerabilities`, `license_compliance`) now run their own focused Trivy scans; `supply_chain` reads components from the SBOM.

**Tech Stack:** Python 3.12, LangGraph, asyncio, Docker/Trivy, MongoDB/Motor, pytest + pytest-asyncio

---

## File Map

| Action | Path |
|---|---|
| New | `backend/src/utils/trivy.py` |
| New | `backend/src/main_graph/subgraphs/discovery/models.py` |
| New | `backend/src/main_graph/subgraphs/discovery/dao.py` |
| New | `backend/src/main_graph/subgraphs/discovery/nodes/generate_sbom.py` |
| Rewrite | `backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py` |
| Simplify | `backend/src/main_graph/subgraphs/discovery/nodes/fetch_repository.py` |
| Delete | `backend/src/main_graph/subgraphs/discovery/nodes/parse_package_files.py` |
| Update | `backend/src/main_graph/subgraphs/discovery/nodes/__init__.py` |
| Update | `backend/src/main_graph/subgraphs/discovery/constants.py` |
| Update | `backend/src/main_graph/subgraphs/discovery/state.py` |
| Update | `backend/src/main_graph/subgraphs/discovery/graph.py` |
| Update | `backend/src/main_graph/state.py` |
| Update | `backend/src/main_graph/subgraphs/orchestrator/state.py` |
| Update | `backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py` |
| Update | `backend/src/main_graph/subgraphs/orchestrator/nodes/planner.py` |
| Update | `backend/src/main_graph/subgraphs/orchestrator/nodes/orchestrator.py` |
| Update | `backend/src/main_graph/nodes/execute_plan.py` |
| Update | `backend/src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py` |
| Update | `backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/nodes/analyze.py` |
| Update | `backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/graph.py` |
| Update | `backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/nodes/analyze.py` |
| Update | `backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/graph.py` |
| Update | `backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py` |
| Delete | `backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_gen/` (entire dir) |
| New | `backend/tests/unit/subgraphs/discovery/test_generate_sbom.py` |
| New | `backend/tests/unit/subgraphs/discovery/test_build_dependency_summary.py` |
| New | `backend/tests/unit/subgraphs/discovery/test_fetch_repository.py` |
| New | `backend/tests/unit/utils/test_trivy.py` |

---

## Task 1: Shared Trivy Runner Utility

**Files:**
- Create: `backend/src/utils/trivy.py`
- Create: `backend/tests/unit/utils/__init__.py`
- Create: `backend/tests/unit/utils/test_trivy.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/utils/test_trivy.py
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.trivy import run_trivy


@pytest.mark.asyncio
async def test_run_trivy_returns_parsed_json():
    sample = {"bomFormat": "CycloneDX", "components": []}
    encoded = json.dumps(sample).encode()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(encoded, b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result, stderr = await run_trivy("/tmp/repo", "--format", "cyclonedx")

    assert result == sample
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_trivy_raises_on_nonzero_exit():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="Trivy exited 1"):
            await run_trivy("/tmp/repo", "--format", "cyclonedx")


@pytest.mark.asyncio
async def test_run_trivy_raises_on_timeout():
    import asyncio as _asyncio

    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.communicate = AsyncMock(side_effect=_asyncio.TimeoutError())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", side_effect=TimeoutError()):
        with pytest.raises(RuntimeError, match="timed out"):
            await run_trivy("/tmp/repo", "--format", "cyclonedx")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/utils/test_trivy.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.utils.trivy'`

- [ ] **Step 3: Create the utility**

```python
# backend/src/utils/trivy.py
"""Shared Trivy Docker runner."""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_TRIVY_IMAGE = "aquasec/trivy:latest"
_TRIVY_TIMEOUT = 300


async def run_trivy(repo_path: str, *trivy_args: str) -> tuple[dict, str]:
    """Run a Trivy Docker command and return (parsed_json, raw_stderr)."""
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{repo_path}:/repo",
        _TRIVY_IMAGE,
        "fs", "--quiet",
        *trivy_args,
        "/repo",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TRIVY_TIMEOUT
        )
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"Trivy timed out after {_TRIVY_TIMEOUT}s")

    stderr_str = stderr.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Trivy exited {proc.returncode}: {stderr_str.strip()[:500]}")

    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return {}, stderr_str

    return json.loads(raw), stderr_str
```

Also create `backend/tests/unit/utils/__init__.py` (empty).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/utils/test_trivy.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/utils/trivy.py backend/tests/unit/utils/
git commit -m "feat: add shared trivy runner utility"
```

---

## Task 2: Discovery Models + DAO

**Files:**
- Create: `backend/src/main_graph/subgraphs/discovery/models.py`
- Create: `backend/src/main_graph/subgraphs/discovery/dao.py`

- [ ] **Step 1: Create models**

```python
# backend/src/main_graph/subgraphs/discovery/models.py
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SbomEntry(BaseModel):
    """Stored per analysis run in the `sbom_gens` collection."""

    repo_url: str = ""
    sbom_cyclonedx: dict[str, Any] = Field(default_factory=dict)
    scan_error: str | None = None
```

- [ ] **Step 2: Create DAO**

```python
# backend/src/main_graph/subgraphs/discovery/dao.py
from bson import ObjectId

from src.db.connection import get_db
from src.main_graph.subgraphs.discovery.models import SbomEntry


class SbomDAO:
    @property
    def _col(self):
        return get_db()["sbom_gens"]

    async def save(self, entry: SbomEntry) -> str:
        result = await self._col.insert_one(entry.model_dump())
        return str(result.inserted_id)

    async def get(self, doc_id: str) -> dict | None:
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc


sbom_dao = SbomDAO()
```

- [ ] **Step 3: Verify imports**

```bash
cd backend && uv run python -c "from src.main_graph.subgraphs.discovery.dao import sbom_dao; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/src/main_graph/subgraphs/discovery/models.py \
        backend/src/main_graph/subgraphs/discovery/dao.py
git commit -m "feat: add discovery sbom models and dao"
```

---

## Task 3: Update All State Schemas

**Files:**
- Modify: `backend/src/main_graph/subgraphs/discovery/state.py`
- Modify: `backend/src/main_graph/subgraphs/orchestrator/state.py`
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py`
- Modify: `backend/src/main_graph/state.py`

- [ ] **Step 1: Update `DiscoveryState`**

Replace the entire file:

```python
# backend/src/main_graph/subgraphs/discovery/state.py
"""State schema for the ProjectDiscovery subgraph."""

from typing import Any, NotRequired

from typing_extensions import TypedDict


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int


class DiscoveryState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────
    repo_url: str
    concern: str

    # ── Internal: set by fetch_repository ───────────────────────────────
    repo_path: NotRequired[str]

    # ── Internal: set by generate_sbom ──────────────────────────────────
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    manifest_files: NotRequired[list[str]]

    # ── Outputs: set by build_dependency_summary ─────────────────────────
    project_metadata: NotRequired[ProjectMetadata]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
    sbom_error: NotRequired[str | None]
```

- [ ] **Step 2: Update `OrchestratorState`**

Replace the entire file:

```python
# backend/src/main_graph/subgraphs/orchestrator/state.py
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class OrchestratorState(TypedDict):
    # ── Inputs from parent graph ─────────────────────────────────────────────
    concern: str
    sbom_cyclonedx: dict[str, Any]
    discovery_summary: str
    job_id: str

    # ── Outputs to parent graph ──────────────────────────────────────────────
    messages: Annotated[list, add_messages]
    plan: NotRequired[list[str]]
    cancelled: NotRequired[bool]

    # ── Internal: orchestrator → planner on "change" ─────────────────────────
    extra_instructions: NotRequired[str]
```

- [ ] **Step 3: Update `AnalysisState`**

Replace the entire file:

```python
# backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py
from typing import Any, NotRequired

from typing_extensions import TypedDict


class AnalysisState(TypedDict):
    sbom_cyclonedx: dict[str, Any]
    discovery_summary: str
    concern: str
    upstream_results: NotRequired[dict[str, Any]]
    repo_path: NotRequired[str]
```

- [ ] **Step 4: Update `MainState`**

Replace the entire file:

```python
# backend/src/main_graph/state.py
"""State schemas for the main graph."""

import operator
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import ProjectMetadata


class MainState(TypedDict):
    # ── Inputs (provided by job_runner) ─────────────────────────────────────
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

    # ── Orchestrator: conversation history ───────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Orchestrator: approved plan ──────────────────────────────────────────
    plan: NotRequired[list[str]]

    # ── Staged execution ─────────────────────────────────────────────────────
    execution_stages: NotRequired[list[list[str]]]
    current_stage_index: NotRequired[int]

    # ── Parallel reducer ─────────────────────────────────────────────────────
    subgraph_results: Annotated[list[dict], operator.add]

    # ── Temp fields set by task_dispatcher via Send ──────────────────────────
    subgraph_name: NotRequired[str]
    upstream_results: NotRequired[dict]

    # ── Cross-analyzer and report-reviewer outputs ────────────────────────────
    analysis_report: NotRequired[dict[str, Any]]
    reviewer_feedback: NotRequired[str]
    review_approved: NotRequired[bool]
    review_iterations: NotRequired[int]
    cancelled: NotRequired[bool]
```

- [ ] **Step 5: Verify imports compile**

```bash
cd backend && uv run python -c "
from src.main_graph.state import MainState
from src.main_graph.subgraphs.orchestrator.state import OrchestratorState
from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState
from src.main_graph.subgraphs.discovery.state import DiscoveryState
print('all states ok')
"
```

Expected: `all states ok`

- [ ] **Step 6: Commit**

```bash
git add backend/src/main_graph/subgraphs/discovery/state.py \
        backend/src/main_graph/subgraphs/orchestrator/state.py \
        backend/src/main_graph/subgraphs/ingestion_subgraphs/_base.py \
        backend/src/main_graph/state.py
git commit -m "refactor: replace dep lists with sbom_cyclonedx in all state schemas"
```

---

## Task 4: Add `GENERATE_SBOM` Constant

**Files:**
- Modify: `backend/src/main_graph/subgraphs/discovery/constants.py`

Note: `PARSE_PACKAGE_FILES` is kept here until Task 8, when `graph.py` and `nodes/__init__.py` are updated atomically. Removing it now would break the current graph import.

- [ ] **Step 1: Add constant**

```python
# backend/src/main_graph/subgraphs/discovery/constants.py
"""Node name constants for the ProjectDiscovery subgraph."""

FETCH_REPOSITORY = "fetch_repository"
GENERATE_SBOM = "generate_sbom"
PARSE_PACKAGE_FILES = "parse_package_files"  # removed in Task 8
BUILD_DEPENDENCY_SUMMARY = "build_dependency_summary"
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/main_graph/subgraphs/discovery/constants.py
git commit -m "refactor: add GENERATE_SBOM constant"
```

---

## Task 5: Simplify `fetch_repository`

**Files:**
- Modify: `backend/src/main_graph/subgraphs/discovery/nodes/fetch_repository.py`
- Create: `backend/tests/unit/subgraphs/discovery/__init__.py`
- Create: `backend/tests/unit/subgraphs/__init__.py`
- Create: `backend/tests/unit/subgraphs/discovery/test_fetch_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/subgraphs/discovery/test_fetch_repository.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.fetch_repository import fetch_repository


@pytest.mark.asyncio
async def test_fetch_repository_returns_repo_path_on_success():
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b""))):
        result = await fetch_repository({"repo_url": "https://github.com/test/repo", "concern": ""})

    assert "repo_path" in result
    assert result["repo_path"].startswith("/")
    assert "package_json_content" not in result
    assert "lock_file_content" not in result
    assert "lock_file_name" not in result


@pytest.mark.asyncio
async def test_fetch_repository_error_on_empty_url():
    result = await fetch_repository({"repo_url": "", "concern": ""})
    assert result == {"discovery_error": "No repository URL provided"}


@pytest.mark.asyncio
async def test_fetch_repository_error_on_clone_failure():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"repository not found"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b"repository not found"))):
        result = await fetch_repository({"repo_url": "https://github.com/bad/url", "concern": ""})

    assert "discovery_error" in result
    assert "git clone failed" in result["discovery_error"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/subgraphs/discovery/test_fetch_repository.py -v
```

Expected: FAIL — current `fetch_repository` returns `package_json_content` etc.

- [ ] **Step 3: Replace `fetch_repository.py`**

```python
# backend/src/main_graph/subgraphs/discovery/nodes/fetch_repository.py
"""Node: fetch_repository — git clone into a temp directory."""

import asyncio
import logging
import tempfile

from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120


async def fetch_repository(state: DiscoveryState) -> dict:
    repo_url = state.get("repo_url", "").strip()

    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = tempfile.mkdtemp(prefix="misi_repo_")
    logger.info("fetch_repository: cloning %s into %s", repo_url, tmp_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", "--single-branch",
            repo_url, tmp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_CLONE_TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            return {"discovery_error": f"git clone timed out after {_CLONE_TIMEOUT}s"}

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()[:300]
            logger.error("fetch_repository: git clone failed: %s", err_msg)
            return {"discovery_error": f"git clone failed: {err_msg}"}

        logger.info("fetch_repository: cloned %s into %s", repo_url, tmp_dir)
        return {"repo_path": tmp_dir}

    except Exception as exc:  # noqa: BLE001
        logger.exception("fetch_repository: unexpected error for %s", repo_url)
        return {"discovery_error": f"Repository fetch failed: {exc}"}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/subgraphs/discovery/test_fetch_repository.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/main_graph/subgraphs/discovery/nodes/fetch_repository.py \
        backend/tests/unit/subgraphs/
git commit -m "refactor: simplify fetch_repository to clone-only"
```

---

## Task 6: Create `generate_sbom` Node

**Files:**
- Create: `backend/src/main_graph/subgraphs/discovery/nodes/generate_sbom.py`
- Create: `backend/tests/unit/subgraphs/discovery/test_generate_sbom.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/subgraphs/discovery/test_generate_sbom.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.generate_sbom import generate_sbom

_SAMPLE_SBOM = {
    "bomFormat": "CycloneDX",
    "metadata": {"component": {"name": "my-app", "bom-ref": "my-app"}},
    "components": [{"bom-ref": "pkg:npm/express@4.18.2", "name": "express", "version": "4.18.2"}],
    "dependencies": [{"ref": "my-app", "dependsOn": ["pkg:npm/express@4.18.2"]}],
}


@pytest.mark.asyncio
async def test_generate_sbom_success():
    state = {"repo_url": "https://github.com/test/repo", "concern": "", "repo_path": "/tmp/repo"}

    with patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_trivy",
               new=AsyncMock(return_value=(_SAMPLE_SBOM, ""))) as mock_trivy, \
         patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao") as mock_dao, \
         patch("pathlib.Path.exists", return_value=True):
        mock_dao.save = AsyncMock(return_value="abc123")
        result = await generate_sbom(state)

    mock_trivy.assert_awaited_once_with("/tmp/repo", "--format", "cyclonedx")
    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM
    assert result["sbom_result_id"] == "abc123"
    assert "sbom_error" not in result


@pytest.mark.asyncio
async def test_generate_sbom_trivy_failure_still_saves():
    state = {"repo_url": "https://github.com/test/repo", "concern": "", "repo_path": "/tmp/repo"}

    with patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_trivy",
               new=AsyncMock(side_effect=RuntimeError("Trivy timed out after 300s"))), \
         patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao") as mock_dao, \
         patch("pathlib.Path.exists", return_value=False):
        mock_dao.save = AsyncMock(return_value="err123")
        result = await generate_sbom(state)

    assert result["sbom_cyclonedx"] == {}
    assert result["sbom_error"] == "Trivy timed out after 300s"
    assert result["sbom_result_id"] == "err123"
    mock_dao.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_sbom_no_repo_path():
    state = {"repo_url": "", "concern": "", "repo_path": ""}

    with patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao") as mock_dao:
        mock_dao.save = AsyncMock(return_value="no-path-id")
        result = await generate_sbom(state)

    assert result["sbom_error"] == "repo_path not available"
    assert result["sbom_cyclonedx"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/subgraphs/discovery/test_generate_sbom.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.discovery.nodes.generate_sbom'`

- [ ] **Step 3: Create `generate_sbom.py`**

```python
# backend/src/main_graph/subgraphs/discovery/nodes/generate_sbom.py
"""Node: generate_sbom — run Trivy CycloneDX scan and persist the SBOM."""

import logging
from pathlib import Path

from src.main_graph.subgraphs.discovery.dao import sbom_dao
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_MANIFESTS = ("package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json")


def _detect_manifest_files(repo_path: str) -> list[str]:
    root = Path(repo_path)
    return [name for name in _MANIFESTS if (root / name).exists()]


async def generate_sbom(state: DiscoveryState) -> dict:
    repo_path = state.get("repo_path", "")

    if not repo_path:
        logger.error("generate_sbom: no repo_path in state")
        entry = SbomEntry(repo_url=state.get("repo_url", ""), scan_error="repo_path not available")
        result_id = await sbom_dao.save(entry)
        return {"sbom_cyclonedx": {}, "sbom_result_id": result_id, "manifest_files": [], "sbom_error": "repo_path not available"}

    sbom_data: dict = {}
    sbom_error: str | None = None

    try:
        logger.info("generate_sbom: running Trivy CycloneDX scan on %s", repo_path)
        sbom_data, _ = await run_trivy(repo_path, "--format", "cyclonedx")
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_sbom: scan failed")
        sbom_error = str(exc)

    manifest_files = _detect_manifest_files(repo_path)

    entry = SbomEntry(
        repo_url=state.get("repo_url", ""),
        sbom_cyclonedx=sbom_data,
        scan_error=sbom_error,
    )
    result_id = await sbom_dao.save(entry)
    logger.info("generate_sbom: saved — result_id=%s error=%s", result_id, sbom_error)

    result: dict = {
        "sbom_cyclonedx": sbom_data,
        "sbom_result_id": result_id,
        "manifest_files": manifest_files,
    }
    if sbom_error:
        result["sbom_error"] = sbom_error
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/subgraphs/discovery/test_generate_sbom.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Verify nodes `__init__` now imports cleanly**

```bash
cd backend && uv run python -c "from src.main_graph.subgraphs.discovery.nodes import generate_sbom; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add backend/src/main_graph/subgraphs/discovery/nodes/generate_sbom.py \
        backend/tests/unit/subgraphs/discovery/test_generate_sbom.py
git commit -m "feat: add generate_sbom node (Trivy CycloneDX)"
```

---

## Task 7: Rewrite `build_dependency_summary`

**Files:**
- Rewrite: `backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`
- Create: `backend/tests/unit/subgraphs/discovery/test_build_dependency_summary.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/subgraphs/discovery/test_build_dependency_summary.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.build_dependency_summary import (
    build_dependency_summary,
)

_SAMPLE_SBOM = {
    "metadata": {
        "component": {"name": "my-app", "bom-ref": "my-app"}
    },
    "components": [
        {"bom-ref": "pkg:npm/express@4.18.2", "name": "express", "version": "4.18.2"},
        {"bom-ref": "pkg:npm/accepts@1.3.8", "name": "accepts", "version": "1.3.8"},
    ],
    "dependencies": [
        {"ref": "my-app", "dependsOn": ["pkg:npm/express@4.18.2"]},
        {"ref": "pkg:npm/express@4.18.2", "dependsOn": ["pkg:npm/accepts@1.3.8"]},
    ],
}


@pytest.mark.asyncio
async def test_extracts_project_metadata_from_cyclonedx():
    state = {
        "sbom_cyclonedx": _SAMPLE_SBOM,
        "manifest_files": ["package.json", "pnpm-lock.yaml"],
        "concern": "security",
        "repo_url": "https://github.com/test/repo",
    }

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.build_dependency_summary._llm"
    ) as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="A great summary"))
        result = await build_dependency_summary(state)

    assert result["project_metadata"]["name"] == "my-app"
    assert result["project_metadata"]["package_manager"] == "pnpm"
    assert result["project_metadata"]["direct_dependencies_count"] == 1
    assert result["discovery_summary"] == "A great summary"


@pytest.mark.asyncio
async def test_returns_failure_summary_on_sbom_error():
    state = {
        "sbom_error": "Trivy timed out after 300s",
        "concern": "security",
        "repo_url": "",
    }

    result = await build_dependency_summary(state)

    assert result["project_metadata"]["name"] == "unknown"
    assert result["project_metadata"]["direct_dependencies_count"] == 0
    assert "Trivy timed out" in result["discovery_summary"]


@pytest.mark.asyncio
async def test_returns_failure_summary_on_discovery_error():
    state = {
        "discovery_error": "git clone failed",
        "concern": "security",
        "repo_url": "",
    }

    result = await build_dependency_summary(state)

    assert result["project_metadata"]["name"] == "unknown"
    assert "git clone failed" in result["discovery_summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/subgraphs/discovery/test_build_dependency_summary.py -v
```

Expected: FAIL — current version reads from `parsed_manifests`, not `sbom_cyclonedx`

- [ ] **Step 3: Rewrite `build_dependency_summary.py`**

```python
# backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py
"""Node: build_dependency_summary — generate metadata and LLM summary from CycloneDX."""

from typing import Any

from src.main_graph.subgraphs.discovery.state import (
    DiscoveryState,
    ProjectMetadata,
)
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_4O_MINI)


def _get_root_ref(sbom: dict[str, Any]) -> str:
    root = sbom.get("metadata", {}).get("component", {})
    return root.get("bom-ref") or root.get("name", "")


def _get_direct_dep_refs(sbom: dict[str, Any], root_ref: str) -> set[str]:
    for entry in sbom.get("dependencies", []):
        if entry.get("ref") == root_ref:
            return set(entry.get("dependsOn", []))
    return set()


def _get_project_name(sbom: dict[str, Any]) -> str:
    return sbom.get("metadata", {}).get("component", {}).get("name", "unknown")


def _detect_package_manager(manifest_files: list[str]) -> str:
    for f in manifest_files:
        if "pnpm" in f:
            return "pnpm"
        if "yarn" in f:
            return "yarn"
        if "package-lock" in f:
            return "npm"
    return "npm"


def _build_prompt(
    project_name: str,
    concern: str,
    pm: str,
    direct_names: list[str],
    direct_count: int,
    total_count: int,
    transitive_count: int,
) -> str:
    def _fmt(names: list[str], limit: int = 20) -> str:
        items = names[:limit]
        result = ", ".join(items)
        if len(names) > limit:
            result += f", … and {len(names) - limit} more"
        return result or "none"

    return f"""\
You are analyzing the dependency structure of a JavaScript/Node.js project.

Project: {project_name}
Package manager: {pm}
Direct dependencies ({direct_count}): {_fmt(direct_names)}
Transitive dependencies: {transitive_count}
Total components: {total_count}
Analysis concern: {concern}

Write a concise summary (3–5 sentences) that:
- Describes the package management approach and overall dependency structure
- Characterizes the ecosystem (e.g., frontend-heavy, backend services, tooling, etc.)
- Highlights notable dependencies relevant to the concern: "{concern}"
- Explains why those dependencies matter in this context

Focus on interpretation over enumeration. Do not list all dependencies.
Output only the summary text.\
"""


async def build_dependency_summary(state: DiscoveryState) -> dict:
    error = state.get("discovery_error") or state.get("sbom_error")
    if error:
        return {
            "project_metadata": ProjectMetadata(
                name="unknown",
                package_manager="unknown",
                direct_dependencies_count=0,
            ),
            "discovery_summary": f"Discovery failed: {error}",
        }

    sbom: dict[str, Any] = state.get("sbom_cyclonedx", {})
    manifest_files: list[str] = state.get("manifest_files", [])
    concern: str = state.get("concern", "")

    root_ref = _get_root_ref(sbom)
    direct_refs = _get_direct_dep_refs(sbom, root_ref)
    components = sbom.get("components", [])

    direct_names = [c["name"] for c in components if c.get("bom-ref") in direct_refs]
    transitive_count = len(components) - len(direct_refs)

    pm = _detect_package_manager(manifest_files)
    project_name = _get_project_name(sbom)

    metadata = ProjectMetadata(
        name=project_name,
        package_manager=pm,
        direct_dependencies_count=len(direct_refs),
    )

    prompt = _build_prompt(
        project_name, concern, pm, direct_names,
        len(direct_refs), len(components), transitive_count,
    )
    response = await _llm.ainvoke(prompt)

    return {
        "project_metadata": metadata,
        "discovery_summary": response.content,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/subgraphs/discovery/test_build_dependency_summary.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py \
        backend/tests/unit/subgraphs/discovery/test_build_dependency_summary.py
git commit -m "refactor: rewrite build_dependency_summary to read from CycloneDX"
```

---

## Task 8: Update Discovery Graph, Constants, and Delete `parse_package_files`

All three changes are committed together — removing `PARSE_PACKAGE_FILES` and updating `nodes/__init__.py` would break the old graph before `graph.py` is updated. One atomic commit keeps every state valid.

**Files:**
- Rewrite: `backend/src/main_graph/subgraphs/discovery/graph.py`
- Update: `backend/src/main_graph/subgraphs/discovery/constants.py` (remove `PARSE_PACKAGE_FILES`)
- Update: `backend/src/main_graph/subgraphs/discovery/nodes/__init__.py`
- Delete: `backend/src/main_graph/subgraphs/discovery/nodes/parse_package_files.py`

- [ ] **Step 1: Final `constants.py` — remove `PARSE_PACKAGE_FILES`**

```python
# backend/src/main_graph/subgraphs/discovery/constants.py
"""Node name constants for the ProjectDiscovery subgraph."""

FETCH_REPOSITORY = "fetch_repository"
GENERATE_SBOM = "generate_sbom"
BUILD_DEPENDENCY_SUMMARY = "build_dependency_summary"
```

- [ ] **Step 2: Update `nodes/__init__.py`**

```python
# backend/src/main_graph/subgraphs/discovery/nodes/__init__.py
from src.main_graph.subgraphs.discovery.nodes.build_dependency_summary import (
    build_dependency_summary,
)
from src.main_graph.subgraphs.discovery.nodes.fetch_repository import (
    fetch_repository,
)
from src.main_graph.subgraphs.discovery.nodes.generate_sbom import (
    generate_sbom,
)

__all__ = [
    "fetch_repository",
    "generate_sbom",
    "build_dependency_summary",
]
```

- [ ] **Step 3: Rewrite `graph.py`**

```python
# backend/src/main_graph/subgraphs/discovery/graph.py
"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    FETCH_REPOSITORY,
    GENERATE_SBOM,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    fetch_repository,
    generate_sbom,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _after_fetch(state: DiscoveryState) -> str:
    return (
        BUILD_DEPENDENCY_SUMMARY
        if state.get("discovery_error")
        else GENERATE_SBOM
    )


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(FETCH_REPOSITORY, fetch_repository)
    builder.add_node(GENERATE_SBOM, generate_sbom)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, FETCH_REPOSITORY)
    builder.add_conditional_edges(
        FETCH_REPOSITORY,
        _after_fetch,
        [GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY],
    )
    builder.add_edge(GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
```

- [ ] **Step 4: Delete `parse_package_files.py`**

```bash
rm backend/src/main_graph/subgraphs/discovery/nodes/parse_package_files.py
```

- [ ] **Step 5: Verify graph builds**

```bash
cd backend && uv run python -c "from src.main_graph.subgraphs.discovery.graph import discovery_subgraph; print('graph ok')"
```

Expected: `graph ok`

- [ ] **Step 6: Commit all four changes together**

```bash
git add backend/src/main_graph/subgraphs/discovery/graph.py \
        backend/src/main_graph/subgraphs/discovery/constants.py \
        backend/src/main_graph/subgraphs/discovery/nodes/__init__.py
git rm backend/src/main_graph/subgraphs/discovery/nodes/parse_package_files.py
git commit -m "refactor: update discovery graph topology (fetch → generate_sbom → summary)"
```

---

## Task 9: Update Orchestrator Layer

**Files:**
- Modify: `backend/src/main_graph/subgraphs/orchestrator/nodes/planner.py`
- Modify: `backend/src/main_graph/subgraphs/orchestrator/nodes/orchestrator.py`
- Modify: `backend/src/main_graph/nodes/execute_plan.py`

- [ ] **Step 1: Update `planner.py`**

Replace the entire file (the system prompt string also references "direct and transitive dependencies" — update it):

```python
```python
# backend/src/main_graph/subgraphs/orchestrator/nodes/planner.py
"""Planner node — selects analysis subgraphs via LLM."""

import json
import logging

from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DESCRIPTIONS,
    SUBGRAPH_REGISTRY,
)
from src.main_graph.subgraphs.orchestrator.state import OrchestratorState
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

VALID_SUBGRAPHS: set[str] = set(SUBGRAPH_REGISTRY.keys())
_FALLBACK_PLAN: list[str] = list(SUBGRAPH_REGISTRY.keys())[:2]


def _build_system_prompt() -> str:
    subgraph_lines = "\n".join(
        f"- {name}: {desc}"
        for entry in SUBGRAPH_DESCRIPTIONS
        for name, desc in [entry.split(":", 1)]
    )
    example = json.dumps(list(SUBGRAPH_REGISTRY.keys())[:2])
    return (
        "You are a dependency analysis planner. Given a project's dependency"
        " discovery summary, its SBOM component list, and a user concern, decide"
        " which analysis subgraphs to run. Available subgraphs:\n"
        f"{subgraph_lines}\n"
        f"Return ONLY a valid JSON array of subgraph names, e.g.: {example}\n"
        "Choose only the subgraphs relevant to the user's concern.\n"
        "If additional instructions are provided, honor them —\n"
        "they reflect updated user preferences."
    )


_SYSTEM_PROMPT = _build_system_prompt()


async def run_planner(
    state: OrchestratorState, extra_instructions: str = ""
) -> list[str]:
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    sbom = state.get("sbom_cyclonedx", {})

    components = sbom.get("components", [])
    dep_list = ", ".join(c["name"] for c in components[:20])
    if len(components) > 20:
        dep_list += f", and {len(components) - 20} more"

    user_message = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Total components ({len(components)}): {dep_list}"
    )
    if extra_instructions:
        user_message += (
            f"\n\nAdditional instructions from the user: {extra_instructions}"
        )

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    )

    try:
        plan = parse_llm_json(response.content or "")
        plan = [s for s in plan if s in VALID_SUBGRAPHS]
        if not plan:
            plan = _FALLBACK_PLAN
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("planner: failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("planner: selected subgraphs: %s", plan)
    return plan


async def planner(state: OrchestratorState) -> dict:
    plan = await run_planner(
        state, extra_instructions=state.get("extra_instructions", "")
    )
    return {"plan": plan, "extra_instructions": ""}
```
```

- [ ] **Step 2: Update `orchestrator.py`**

In `_present_plan` (around line 45), replace the `direct_dependencies` reference:

```python
async def _present_plan(plan: list[str], state: OrchestratorState, context: str) -> str:
    plan_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan))
    sbom = state.get("sbom_cyclonedx", {})
    user_content = (
        f"Project concern: {state.get('concern', 'not specified')}\n"
        f"Total components: {len(sbom.get('components', []))}\n"
    )
    if context:
        user_content += f"\nPrior conversation context:\n{context}\n"
    user_content += f"\nProposed analysis plan:\n{plan_str}"

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _PRESENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )
    return response.content
```

Also update the `interrupt` call (around line 109) to remove `direct_dependencies_count`:

```python
    user_input: str = interrupt(
        {
            "plan": plan,
            "assistant_message": assistant_msg,
            "discovery_summary": state.get("discovery_summary", ""),
            "total_components": len(state.get("sbom_cyclonedx", {}).get("components", [])),
        }
    )
```

- [ ] **Step 3: Update `execute_plan.py`**

Replace the `invocation` dict (around line 41):

```python
        invocation: dict = {
            "sbom_cyclonedx": state.get("sbom_cyclonedx", {}),
            "discovery_summary": state.get("discovery_summary", ""),
            "concern": state.get("concern", ""),
            "upstream_results": hydrated_upstream,
        }
        if repo_path := state.get("repo_path"):
            invocation["repo_path"] = repo_path
```

- [ ] **Step 4: Verify imports**

```bash
cd backend && uv run python -c "
from src.main_graph.subgraphs.orchestrator.nodes.planner import planner
from src.main_graph.subgraphs.orchestrator.nodes.orchestrator import orchestrator
from src.main_graph.nodes.execute_plan import execute_plan
print('orchestrator layer ok')
"
```

Expected: `orchestrator layer ok`

- [ ] **Step 5: Commit**

```bash
git add backend/src/main_graph/subgraphs/orchestrator/nodes/planner.py \
        backend/src/main_graph/subgraphs/orchestrator/nodes/orchestrator.py \
        backend/src/main_graph/nodes/execute_plan.py
git commit -m "refactor: update orchestrator layer to use sbom_cyclonedx"
```

---

## Task 10: Update `supply_chain` Node

**Files:**
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py`

- [ ] **Step 1: Update `analyze.py`**

Replace the entire file:

```python
# backend/src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py
"""Supply chain analysis node."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.dao import (
    supply_chain_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.models import (
    SupplyChainEntry,
    SupplyChainRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.state import (
    SupplyChainState,
)

logger = logging.getLogger(__name__)


async def analyze(state: SupplyChainState) -> dict:
    sbom = state.get("sbom_cyclonedx", {})
    concern = state.get("concern", "")
    components = sbom.get("components", [])

    records = [
        SupplyChainRecord(
            name=comp["name"],
            version=comp.get("version", "unknown"),
            risk_score=0.1,
            flags=["mock-data"],
        )
        for comp in components[:10]
    ]

    entry = SupplyChainEntry(
        records=records,
        high_risk_count=sum(1 for r in records if r.risk_score >= 0.7),
        concern=concern,
    )
    result_id = await supply_chain_dao.save(entry)
    logger.info("supply_chain: saved %d records, result_id=%s", len(records), result_id)
    return {"result_id": result_id}
```

- [ ] **Step 2: Verify import**

```bash
cd backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.nodes.analyze import analyze; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py
git commit -m "refactor: supply_chain reads components from sbom_cyclonedx"
```

---

## Task 11: Update `vulnerabilities` Node

**Files:**
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/nodes/analyze.py`
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/graph.py`

- [ ] **Step 1: Rewrite `analyze.py`**

```python
# backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/nodes/analyze.py
"""Vulnerabilities analysis node — runs its own Trivy vuln scan."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.models import (
    VulnerabilitiesEntry,
    VulnerabilityFinding,
    VulnerabilityRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.state import (
    VulnerabilitiesState,
)
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)


def _severity_rank(s: str) -> int:
    return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s.upper(), 0)


def _build_records(raw_vulns: list[dict]) -> list[VulnerabilityRecord]:
    by_pkg: dict[str, list[VulnerabilityFinding]] = {}
    versions: dict[str, str] = {}

    for v in raw_vulns:
        pkg = v.get("PkgName", "")
        if not pkg:
            continue
        versions.setdefault(pkg, v.get("InstalledVersion", "unknown"))
        by_pkg.setdefault(pkg, []).append(
            VulnerabilityFinding(
                cve_id=v.get("VulnerabilityID") or None,
                severity=v.get("Severity", "UNKNOWN"),
                description=v.get("Description") or None,
                fixed_in=v.get("FixedVersion") or None,
            )
        )

    return [
        VulnerabilityRecord(
            name=pkg,
            version=versions[pkg],
            findings=findings,
            risk_level=max(findings, key=lambda f: _severity_rank(f.severity)).severity.lower(),
        )
        for pkg, findings in by_pkg.items()
    ]


async def analyze(state: VulnerabilitiesState) -> dict:
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")

    if not repo_path:
        logger.error("vulnerabilities: no repo_path in state")
        entry = VulnerabilitiesEntry(records=[], total_findings=0, concern=concern)
        result_id = await vulnerabilities_dao.save(entry)
        return {"result_id": result_id}

    scan_data: dict = {}
    try:
        logger.info("vulnerabilities: running Trivy vuln scan on %s", repo_path)
        scan_data, _ = await run_trivy(repo_path, "--format", "json", "--scanners", "vuln")
    except Exception as exc:  # noqa: BLE001
        logger.exception("vulnerabilities: Trivy scan failed: %s", exc)

    raw_vulns = [
        v
        for result in scan_data.get("Results", [])
        for v in (result.get("Vulnerabilities") or [])
    ]

    records = _build_records(raw_vulns)
    entry = VulnerabilitiesEntry(
        records=records,
        total_findings=sum(len(r.findings) for r in records),
        concern=concern,
    )
    result_id = await vulnerabilities_dao.save(entry)
    logger.info("vulnerabilities: %d packages, result_id=%s", len(records), result_id)
    return {"result_id": result_id}
```

- [ ] **Step 2: Remove `DEPENDS_ON` sbom_gen in `graph.py`**

Change line 14 in `vulnerabilities/graph.py`:

```python
DEPENDS_ON: list[str] = []
```

- [ ] **Step 3: Verify imports**

```bash
cd backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.graph import vulnerabilities_subgraph; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/nodes/analyze.py \
        backend/src/main_graph/subgraphs/ingestion_subgraphs/vulnerabilities/graph.py
git commit -m "refactor: vulnerabilities runs own Trivy scan, removes sbom_gen dependency"
```

---

## Task 12: Update `license_compliance` Node

**Files:**
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/nodes/analyze.py`
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/graph.py`

- [ ] **Step 1: Rewrite `analyze.py`**

```python
# backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/nodes/analyze.py
"""License compliance analysis node — runs its own Trivy license scan."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.models import (
    LicenseComplianceEntry,
    LicenseRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.state import (
    LicenseComplianceState,
)
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_RISKY_CATEGORIES = {"restricted", "reciprocal", "unknown"}
_RISKY_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.0", "LGPL-2.1"}


def _risk_level(category: str, license_name: str) -> str:
    cat = category.lower()
    if cat in _RISKY_CATEGORIES or license_name in _RISKY_LICENSES:
        return "high"
    if cat in {"notice", "permissive"}:
        return "low"
    return "medium"


def _is_compliant(category: str, license_name: str) -> bool:
    return category.lower() != "restricted" and license_name not in _RISKY_LICENSES


async def analyze(state: LicenseComplianceState) -> dict:
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")

    if not repo_path:
        logger.error("license_compliance: no repo_path in state")
        entry = LicenseComplianceEntry(records=[], total_violations=0, concern=concern)
        result_id = await license_compliance_dao.save(entry)
        return {"result_id": result_id}

    scan_data: dict = {}
    try:
        logger.info("license_compliance: running Trivy license scan on %s", repo_path)
        scan_data, _ = await run_trivy(repo_path, "--format", "json", "--scanners", "license")
    except Exception as exc:  # noqa: BLE001
        logger.exception("license_compliance: Trivy scan failed: %s", exc)

    raw_licenses = [
        lic
        for result in scan_data.get("Results", [])
        for lic in (result.get("Licenses") or [])
    ]

    records = [
        LicenseRecord(
            name=lic.get("PkgName", ""),
            version="",
            license=lic.get("Name") or None,
            is_compliant=_is_compliant(lic.get("Category", "unknown"), lic.get("Name", "")),
            risk_level=_risk_level(lic.get("Category", "unknown"), lic.get("Name", "")),
            notes=f"category={lic.get('Category', 'unknown')}",
        )
        for lic in raw_licenses
        if lic.get("PkgName")
    ]

    entry = LicenseComplianceEntry(
        records=records,
        total_violations=sum(1 for r in records if not r.is_compliant),
        concern=concern,
    )
    result_id = await license_compliance_dao.save(entry)
    logger.info(
        "license_compliance: %d records, %d violations, result_id=%s",
        len(records), entry.total_violations, result_id,
    )
    return {"result_id": result_id}
```

- [ ] **Step 2: Remove `DEPENDS_ON` sbom_gen in `graph.py`**

Change line 16 in `license_compliance/graph.py`:

```python
DEPENDS_ON: list[str] = []
```

- [ ] **Step 3: Verify imports**

```bash
cd backend && uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.graph import license_compliance_subgraph; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/nodes/analyze.py \
        backend/src/main_graph/subgraphs/ingestion_subgraphs/license_compliance/graph.py
git commit -m "refactor: license_compliance runs own Trivy scan, removes sbom_gen dependency"
```

---

## Task 13: Remove `sbom_gen` — Update Registry and Delete Directory

**Files:**
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`
- Delete: `backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_gen/` (entire dir)

- [ ] **Step 1: Update `ingestion_subgraphs/__init__.py`**

```python
# backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py
from src.main_graph.subgraphs.ingestion_subgraphs import (
    license_compliance,
    supply_chain,
    vulnerabilities,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.dao import (
    supply_chain_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)

_MODULES = [vulnerabilities, license_compliance, supply_chain]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}
SUBGRAPH_DAOS = {
    "vulnerabilities": vulnerabilities_dao,
    "license_compliance": license_compliance_dao,
    "supply_chain": supply_chain_dao,
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
    "SUBGRAPH_DAOS",
]
```

- [ ] **Step 2: Verify registry loads**

```bash
cd backend && uv run python -c "
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY, SUBGRAPH_DESCRIPTIONS
print('registry:', list(SUBGRAPH_REGISTRY.keys()))
print('descriptions:', SUBGRAPH_DESCRIPTIONS)
"
```

Expected:
```
registry: ['vulnerabilities', 'license_compliance', 'supply_chain']
descriptions: [...]
```

- [ ] **Step 3: Delete `sbom_gen` directory**

```bash
rm -rf backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_gen
```

- [ ] **Step 4: Verify full app imports cleanly**

```bash
cd backend && uv run python -c "from src.main_graph.graph import build_main_graph; print('main graph ok')"
```

Expected: `main graph ok`

- [ ] **Step 5: Run full test suite**

```bash
cd backend && uv run pytest tests/ -v
```

Expected: all tests pass, no import errors

- [ ] **Step 6: Commit**

```bash
git add backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py
git rm -r backend/src/main_graph/subgraphs/ingestion_subgraphs/sbom_gen
git commit -m "feat: remove sbom_gen subgraph — SBOM generation now in discovery"
```
