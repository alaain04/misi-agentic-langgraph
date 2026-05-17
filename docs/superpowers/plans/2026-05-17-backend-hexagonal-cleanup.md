# Backend Hexagonal Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove npm-dependent dead code and apply hexagonal architecture patterns (port interfaces, central DI wiring) from the workers service to the backend.

**Architecture:** Extract a `JobRepositoryPort` ABC so all infrastructure calls go through an interface; wire the singleton `JobDAO` in a central `api/dependencies.py`; update routes and job_runner to receive the DAO via injection rather than instantiating it inline.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Motor (AsyncMongoClient), pytest-asyncio, uv

---

## File Map

### Task 1 — Delete npm-dependent subgraphs and services

| Action | Path |
|--------|------|
| Delete | `backend/src/services/npm_cache.py` |
| Delete | `backend/src/services/npm_ingestor_client.py` |
| Delete | `backend/src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/` (entire dir) |
| Delete | `backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/` (entire dir) |
| Delete | `backend/src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness/` (entire dir) |
| Modify | `backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py` |
| Modify | `backend/src/main_graph/nodes/planner.py` |
| Modify | `backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py` |

### Task 2 — Extract JobRepositoryPort

| Action | Path |
|--------|------|
| Create | `backend/src/domain/__init__.py` |
| Create | `backend/src/domain/ports/__init__.py` |
| Create | `backend/src/domain/ports/job_repository_port.py` |
| Modify | `backend/src/services/job_dao.py` |

### Task 3 — Central DI wiring + injection

| Action | Path |
|--------|------|
| Create | `backend/src/api/dependencies.py` |
| Modify | `backend/src/api/routes.py` |
| Modify | `backend/src/services/job_runner.py` |
| Modify | `backend/src/main_graph/nodes/execute_plan.py` |

### Task 4 — Tests for injected DAO

| Action | Path |
|--------|------|
| Create | `backend/tests/unit/services/__init__.py` |
| Create | `backend/tests/unit/services/test_job_runner.py` |
| Create | `backend/tests/unit/nodes/test_execute_plan.py` |

---

## Task 1: Remove npm-dependent subgraphs and services

**Context:** `supply_chain`, `registry`, and `dependency_freshness` ingestion subgraphs all import from `npm_cache.py` or `npm_ingestor_client.py`. Removing the services makes those subgraphs dead code. The `registry/` subgraph is not wired in `__init__.py` but exists on disk — delete it too. `cross_analyzer/nodes/analyze.py` references `"supply_chain"` as a string domain — clean it up. `planner.py` builds its prompt and fallback at import time from `SUBGRAPH_DESCRIPTIONS`; removing the deleted subgraphs from the registry automatically removes them from the prompt.

**Files:**
- Delete: `backend/src/services/npm_cache.py`
- Delete: `backend/src/services/npm_ingestor_client.py`
- Delete: `backend/src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/`
- Delete: `backend/src/main_graph/subgraphs/ingestion_subgraphs/registry/`
- Delete: `backend/src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness/`
- Modify: `backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`
- Modify: `backend/src/main_graph/nodes/planner.py`
- Modify: `backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py`

- [ ] **Step 1: Delete the npm service files and the three npm subgraph directories**

```bash
cd backend
rm src/services/npm_cache.py src/services/npm_ingestor_client.py
rm -rf src/main_graph/subgraphs/ingestion_subgraphs/supply_chain
rm -rf src/main_graph/subgraphs/ingestion_subgraphs/registry
rm -rf src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness
```

- [ ] **Step 2: Rewrite `ingestion_subgraphs/__init__.py` to remove the deleted entries**

Replace the full contents of `backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`:

```python
from src.main_graph.subgraphs.ingestion_subgraphs import (
    license_compliance,
    vulnerabilities,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)

_MODULES = [vulnerabilities, license_compliance]

SUBGRAPH_REGISTRY = {mod.GRAPH_NAME: mod.subgraph for mod in _MODULES}
SUBGRAPH_DESCRIPTIONS = [mod.describe() for mod in _MODULES]
SUBGRAPH_DEPENDENCIES: dict[str, list[str]] = {
    mod.GRAPH_NAME: mod.DEPENDS_ON for mod in _MODULES
}
SUBGRAPH_DAOS = {
    "vulnerabilities": vulnerabilities_dao,
    "license_compliance": license_compliance_dao,
}

__all__ = [
    "SUBGRAPH_REGISTRY",
    "SUBGRAPH_DESCRIPTIONS",
    "SUBGRAPH_DEPENDENCIES",
    "SUBGRAPH_DAOS",
]
```

- [ ] **Step 3: Update `planner.py` — remove the npm-specific fallback subgraph names**

The `_FALLBACK_PLAN` currently is `["vulnerabilities", "risk_score", "recommendation"]` which is fine (no npm references). Only change needed: remove the `supply_chain` reference if it appears in the fallback or VALID_SUBGRAPHS. Check with:

```bash
grep -n "supply_chain\|registry\|dependency_freshness" src/main_graph/nodes/planner.py
```

If nothing is found, no edit is needed. If any of those names appear, remove them. The module-level LLM prompt is built from `SUBGRAPH_DESCRIPTIONS`, which is already fixed in Step 2.

- [ ] **Step 4: Clean up `cross_analyzer/nodes/analyze.py` domain grouping**

In `backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py`, the `_group_by_domain` function has a hardcoded `"supply_chain"` key. Update it to only list the remaining domains:

```python
def _group_by_domain(subgraph_results: list[dict]) -> dict:
    domains: dict[str, list] = {
        "vulnerabilities": [],
        "license_compliance": [],
    }
    for item in subgraph_results:
        name = item.get("subgraph", "")
        if name in domains:
            domains[name].append(item)
    return domains
```

- [ ] **Step 5: Verify the backend still imports cleanly**

```bash
cd backend
uv run python -c "from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_REGISTRY; print(list(SUBGRAPH_REGISTRY.keys()))"
```

Expected output: `['vulnerabilities', 'license_compliance']`

- [ ] **Step 6: Run existing tests to confirm nothing broke**

```bash
cd backend
uv run pytest tests/unit -x -q
```

Expected: all existing unit tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/main_graph/subgraphs/ingestion_subgraphs/__init__.py \
        backend/src/main_graph/nodes/planner.py \
        backend/src/main_graph/subgraphs/cross_analyzer/nodes/analyze.py
git commit -m "feat(backend): remove npm-dependent subgraphs and services"
```

---

## Task 2: Extract JobRepositoryPort

**Context:** `JobDAO` is instantiated inline everywhere it's needed (`routes.py`, `job_runner.py`, `execute_plan.py`). Extracting a `JobRepositoryPort` ABC lets callers type-hint against an interface and lets tests inject a mock without hitting MongoDB. `JobDAO` implements the port — no behaviour changes.

**Files:**
- Create: `backend/src/domain/__init__.py`
- Create: `backend/src/domain/ports/__init__.py`
- Create: `backend/src/domain/ports/job_repository_port.py`
- Modify: `backend/src/services/job_dao.py`
- Test: `backend/tests/unit/test_job.py` (run existing, no new file needed)

- [ ] **Step 1: Write a test that imports `JobRepositoryPort` and confirms `JobDAO` is a subtype**

Add to `backend/tests/unit/test_job.py`:

```python
def test_job_dao_implements_port():
    from src.domain.ports.job_repository_port import JobRepositoryPort
    from src.services.job_dao import JobDAO
    assert issubclass(JobDAO, JobRepositoryPort)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend
uv run pytest tests/unit/test_job.py::test_job_dao_implements_port -v
```

Expected: `ModuleNotFoundError: No module named 'src.domain'`

- [ ] **Step 3: Create the domain package and port**

```bash
touch backend/src/domain/__init__.py backend/src/domain/ports/__init__.py
```

Create `backend/src/domain/ports/job_repository_port.py`:

```python
from abc import ABC, abstractmethod

from src.models.job import Job, JobStatus


class JobRepositoryPort(ABC):
    @abstractmethod
    async def create(self, job: Job) -> Job: ...

    @abstractmethod
    async def get(self, job_id: str) -> Job | None: ...

    @abstractmethod
    async def update_status(self, job_id: str, status: JobStatus) -> None: ...

    @abstractmethod
    async def save_result(self, job_id: str, result: dict) -> None: ...

    @abstractmethod
    async def mark_failed(self, job_id: str) -> None: ...

    @abstractmethod
    async def mark_cancelled(self, job_id: str) -> None: ...

    @abstractmethod
    async def start_artifact(self, job_id: str, node: str) -> None: ...

    @abstractmethod
    async def complete_artifact(self, job_id: str, node: str, status: str) -> None: ...

    @abstractmethod
    async def push_proposal(self, job_id: str, proposal: dict) -> None: ...

    @abstractmethod
    async def update_proposal(
        self,
        job_id: str,
        created_at: str,
        user_response: str,
        intent: str,
    ) -> None: ...

    @abstractmethod
    async def update_artifact_data(
        self, job_id: str, node: str, data: dict
    ) -> None: ...

    @abstractmethod
    async def get_pending(self) -> list[Job]: ...

    @abstractmethod
    async def list(
        self,
        page: int = 1,
        limit: int = 10,
        status: JobStatus | None = None,
        trace_id: str | None = None,
    ) -> tuple[list[Job], int]: ...
```

- [ ] **Step 4: Make JobDAO extend the port**

In `backend/src/services/job_dao.py`, add the import and update the class declaration:

```python
# add at the top (after existing imports)
from src.domain.ports.job_repository_port import JobRepositoryPort
```

Change:

```python
class JobDAO:
```

To:

```python
class JobDAO(JobRepositoryPort):
```

No other changes needed — all methods already match the port signatures.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend
uv run pytest tests/unit/test_job.py::test_job_dao_implements_port -v
```

Expected: `PASSED`

- [ ] **Step 6: Run all unit tests**

```bash
cd backend
uv run pytest tests/unit -x -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/domain/ backend/src/services/job_dao.py backend/tests/unit/test_job.py
git commit -m "feat(backend): extract JobRepositoryPort interface, JobDAO implements it"
```

---

## Task 3: Central DI wiring + injection

**Context:** Currently routes call `JobDAO()` inline on each request, and `job_runner.py` / `execute_plan.py` also instantiate `JobDAO()` internally. The workers pattern: one `dependencies.py` file creates the singleton adapters; routes receive them via `Depends()`; functions that need a DAO receive it as a parameter instead of constructing it. For LangGraph nodes (which can't use FastAPI DI), a module-level singleton `_dao` is used — it can be patched in tests.

**Files:**
- Create: `backend/src/api/dependencies.py`
- Modify: `backend/src/api/routes.py`
- Modify: `backend/src/services/job_runner.py`
- Modify: `backend/src/main_graph/nodes/execute_plan.py`

- [ ] **Step 1: Create `backend/src/api/dependencies.py`**

```python
from functools import lru_cache

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.services.job_dao import JobDAO


@lru_cache(maxsize=1)
def get_job_repo() -> JobRepositoryPort:
    return JobDAO()
```

- [ ] **Step 2: Update `backend/src/api/routes.py` to inject the DAO**

Replace the full contents:

```python
import asyncio
import math

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.dependencies import get_job_repo
from src.api.schemas import (
    AnalysisRequest,
    AnalysisStatusResponse,
    ChatRequest,
    JobListItem,
    JobsListResponse,
)
from src.api.service import build_graph_info
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.models.job import Job, JobMetadata, JobStatus
from src.services.job_runner import resume_analysis, run_analysis

router = APIRouter()


@router.post("/analyze", status_code=202)
async def analyze(
    request: AnalysisRequest,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = Job(metadata=JobMetadata(repo_url=request.repo_url, concern=request.concern))
    await dao.create(job)
    asyncio.create_task(
        run_analysis(
            job_id=job.id,
            repo_url=job.metadata.repo_url,
            concern=job.metadata.concern,
            dao=dao,
        )
    )
    return {"trace_id": job.id, "status": job.status}


@router.get("/analyze/{trace_id}", response_model=AnalysisStatusResponse)
async def get_analysis_status(
    trace_id: str,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = await dao.get(trace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="trace_id not found")
    return AnalysisStatusResponse(
        trace_id=job.id,
        status=job.status,
        metadata=job.metadata,
        completed_at=job.completed_at,
        results=job.result,
        artifacts=job.artifacts,
        graph=build_graph_info(job),
    )


@router.post("/analyze/{trace_id}/chat", status_code=202)
async def chat(
    trace_id: str,
    request: ChatRequest,
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    job = await dao.get(trace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="trace_id not found")
    if job.status != JobStatus.awaiting_approval:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not awaiting user input (status: {job.status})",
        )
    asyncio.create_task(
        resume_analysis(job_id=trace_id, user_message=request.message, dao=dao)
    )
    return {"trace_id": trace_id, "status": JobStatus.running}


@router.get("/jobs", response_model=JobsListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: JobStatus | None = Query(None),
    trace_id: str | None = Query(None),
    dao: JobRepositoryPort = Depends(get_job_repo),
):
    jobs, total = await dao.list(page, limit, status=status, trace_id=trace_id)
    pages = math.ceil(total / limit) if total > 0 else 1
    items = [
        JobListItem(
            trace_id=j.id,
            status=j.status,
            concern=j.metadata.concern,
            created_at=j.created_at,
            completed_at=j.completed_at,
        )
        for j in jobs
    ]
    return JobsListResponse(
        items=items, total=total, page=page, limit=limit, pages=pages
    )
```

- [ ] **Step 3: Update `backend/src/services/job_runner.py` to accept `dao` as a parameter**

Replace the full contents:

```python
"""Background task: run a job through the full analysis pipeline."""

import logging
import shutil

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.constants import (
    CROSS_ANALYZER,
    ORCHESTRATOR,
    REPORT_REVIEWER,
)
from src.models.job import JobStatus
from src.services.vector_store import delete_store

logger = logging.getLogger(__name__)

_DISCOVERY_OUTPUT_KEYS = {
    "project_metadata",
    "manifest_files",
    "discovery_summary",
    "discovery_error",
    "sbom_result_id",
    "sbom_error",
}


async def _finalize(dao: JobRepositoryPort, job_id: str, result: dict) -> None:
    delete_store(job_id)
    if repo_path := result.get("repo_path"):
        shutil.rmtree(repo_path, ignore_errors=True)
    if result.get("cancelled"):
        logger.info("job=%s cancelled by user", job_id)
        await dao.mark_cancelled(job_id)
    elif result.get("discovery_error"):
        logger.error("job=%s error=%s", job_id, result["discovery_error"])
        await dao.mark_failed(job_id)
    else:
        logger.info(
            "job=%s done subgraphs=%s",
            job_id,
            [r.get("subgraph") for r in result.get("subgraph_results", [])],
        )
        await dao.save_result(
            job_id,
            {
                "discovery": {
                    k: result[k] for k in _DISCOVERY_OUTPUT_KEYS if k in result
                },
                "plan": result.get("plan", []),
                "subgraph_results": result.get("subgraph_results", []),
                "analysis_report": result.get("analysis_report"),
                "review_approved": result.get("review_approved"),
                "review_iterations": result.get("review_iterations"),
            },
        )


async def _stream_graph(
    graph,
    input_data,
    config,
    dao: JobRepositoryPort,
    job_id: str,
    on_orchestrator_complete=None,
) -> dict | None:
    """Stream graph execution, tracking backbone node artifacts.

    Returns the interrupt payload if the graph paused at the orchestrator,
    or None if the graph ran to completion.
    """
    interrupt_payload = None

    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            if node_name == "__interrupt__":
                interrupt_payload = node_update[0].value
                continue

            if node_name == "discovery":
                await dao.complete_artifact(job_id, "discovery", "done")
                await dao.start_artifact(job_id, ORCHESTRATOR)
            elif node_name == ORCHESTRATOR:
                artifact_status = (
                    "cancelled" if node_update.get("cancelled") else "done"
                )
                await dao.complete_artifact(job_id, ORCHESTRATOR, artifact_status)
                if on_orchestrator_complete and not node_update.get("cancelled"):
                    await on_orchestrator_complete()
            elif node_name == CROSS_ANALYZER:
                await dao.start_artifact(job_id, CROSS_ANALYZER)
                if "analysis_report" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        CROSS_ANALYZER,
                        {"output": node_update["analysis_report"]},
                    )
                await dao.complete_artifact(job_id, CROSS_ANALYZER, "done")
            elif node_name == REPORT_REVIEWER:
                await dao.start_artifact(job_id, REPORT_REVIEWER)
                if "review_approved" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        REPORT_REVIEWER,
                        {
                            "output": {
                                "review_approved": node_update.get("review_approved"),
                                "reviewer_feedback": node_update.get(
                                    "reviewer_feedback"
                                ),
                            }
                        },
                    )
                await dao.complete_artifact(job_id, REPORT_REVIEWER, "done")

    return interrupt_payload


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, "discovery")

    config = {"configurable": {"thread_id": job_id}}

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            {
                "repo_url": repo_url,
                "concern": concern,
                "job_id": job_id,
                "subgraph_results": [],
                "messages": [],
            },
            config,
            dao,
            job_id,
        )

        if interrupt_payload is not None:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error in graph", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id)


async def resume_analysis(
    job_id: str,
    user_message: str,
    dao: JobRepositoryPort,
) -> None:
    """Resume the orchestrator with a plain-text user message."""
    await dao.update_status(job_id, JobStatus.processing)

    config = {"configurable": {"thread_id": job_id}}

    async def _on_approved() -> None:
        await dao.update_status(job_id, JobStatus.running)

    try:
        interrupt_payload = await _stream_graph(
            main_graph,
            Command(resume=user_message),
            config,
            dao,
            job_id,
            on_orchestrator_complete=_on_approved,
        )

        if interrupt_payload is not None:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return

        snapshot = await main_graph.aget_state(config)
        await _finalize(dao, job_id, snapshot.values)

    except Exception:
        logger.exception("job=%s unhandled error on resume", job_id)
        delete_store(job_id)
        await dao.mark_failed(job_id)
```

- [ ] **Step 4: Update `backend/src/main_graph/nodes/execute_plan.py` — replace inline `JobDAO()` with a module-level singleton**

Replace the full contents:

```python
"""Execute plan node — dispatches to the appropriate skeleton subgraph by name."""

import logging

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DAOS,
    SUBGRAPH_REGISTRY,
)
from src.services.job_dao import JobDAO

logger = logging.getLogger(__name__)

_dao: JobRepositoryPort = JobDAO()


async def execute_plan(state: MainState) -> dict:
    name = state.get("subgraph_name", "")
    job_id = state.get("job_id", "")

    subgraph = SUBGRAPH_REGISTRY.get(name)

    if subgraph is None:
        logger.warning("execute_plan: unknown subgraph %r", name)
        if job_id:
            await _dao.complete_artifact(job_id, name, "failed")
        return {"subgraph_results": [{"subgraph": name, "error": "unknown subgraph"}]}

    if job_id:
        await _dao.start_artifact(job_id, name)

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

        result = await subgraph.ainvoke(invocation)

        result_id = result.get("result_id")
        if job_id:
            await _dao.update_artifact_data(job_id, name, {"result_id": result_id})
            await _dao.complete_artifact(job_id, name, "done")
        logger.info("execute_plan: %s completed, result_id=%s", name, result_id)
        return {"subgraph_results": [{"subgraph": name, "result_id": result_id}]}
    except Exception:
        logger.exception("execute_plan: %s failed", name)
        if job_id:
            await _dao.complete_artifact(job_id, name, "failed")
        raise
```

- [ ] **Step 5: Verify the app still imports cleanly**

```bash
cd backend
uv run python -c "from src.api.routes import router; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Run all unit tests**

```bash
cd backend
uv run pytest tests/unit -x -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/api/dependencies.py \
        backend/src/api/routes.py \
        backend/src/services/job_runner.py \
        backend/src/main_graph/nodes/execute_plan.py
git commit -m "feat(backend): central DI wiring — JobDAO injected via Depends and DAO param"
```

---

## Task 4: Tests for injected DAO

**Context:** Now that `run_analysis`, `resume_analysis`, and `execute_plan` receive or use an injectable DAO, we can test them in isolation without MongoDB. Tests use `AsyncMock` for the DAO and mock `main_graph` at the module level.

**Files:**
- Create: `backend/tests/unit/services/__init__.py`
- Create: `backend/tests/unit/services/test_job_runner.py`
- Create: `backend/tests/unit/nodes/test_execute_plan.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/services/__init__.py` (empty).

Create `backend/tests/unit/services/test_job_runner.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.job import JobStatus
from src.services.job_runner import run_analysis, resume_analysis


def _make_dao() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_run_analysis_marks_failed_on_exception():
    dao = _make_dao()

    async def bad_stream(*args, **kwargs):
        raise RuntimeError("graph exploded")
        yield  # makes this an async generator

    with patch("src.services.job_runner.main_graph") as mock_graph, \
         patch("src.services.job_runner.delete_store"):
        mock_graph.astream = bad_stream
        await run_analysis("job-1", "https://github.com/x/y", "security", dao)

    dao.mark_failed.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_run_analysis_sets_awaiting_approval_on_interrupt():
    dao = _make_dao()

    async def interrupt_stream(*args, **kwargs):
        interrupt = MagicMock()
        interrupt.value = {"question": "Approve?", "created_at": "t"}
        yield {"__interrupt__": [interrupt]}

    with patch("src.services.job_runner.main_graph") as mock_graph:
        mock_graph.astream = interrupt_stream
        await run_analysis("job-1", "https://github.com/x/y", "security", dao)

    dao.update_status.assert_any_await("job-1", JobStatus.awaiting_approval)
    dao.save_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_analysis_marks_failed_on_exception():
    dao = _make_dao()

    async def bad_stream(*args, **kwargs):
        raise RuntimeError("resume exploded")
        yield  # makes this an async generator

    with patch("src.services.job_runner.main_graph") as mock_graph, \
         patch("src.services.job_runner.delete_store"):
        mock_graph.astream = bad_stream
        await resume_analysis("job-2", "approve", dao)

    dao.mark_failed.assert_awaited_once_with("job-2")
```

Create `backend/tests/unit/nodes/test_execute_plan.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.nodes.execute_plan import execute_plan


def _make_state(subgraph_name: str = "unknown", job_id: str = "job-1") -> dict:
    return {
        "subgraph_name": subgraph_name,
        "job_id": job_id,
        "sbom_cyclonedx": {},
        "discovery_summary": "",
        "concern": "security",
        "upstream_results": {},
        "subgraph_results": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_execute_plan_unknown_subgraph_records_failure():
    mock_dao = AsyncMock()
    state = _make_state(subgraph_name="does_not_exist", job_id="job-1")

    with patch("src.main_graph.nodes.execute_plan._dao", mock_dao):
        result = await execute_plan(state)

    assert result["subgraph_results"][0]["error"] == "unknown subgraph"
    mock_dao.complete_artifact.assert_awaited_once_with("job-1", "does_not_exist", "failed")


@pytest.mark.asyncio
async def test_execute_plan_no_job_id_skips_artifact_tracking():
    mock_dao = AsyncMock()
    state = _make_state(subgraph_name="does_not_exist", job_id="")

    with patch("src.main_graph.nodes.execute_plan._dao", mock_dao):
        result = await execute_plan(state)

    assert result["subgraph_results"][0]["error"] == "unknown subgraph"
    mock_dao.complete_artifact.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail before any mock is wrong**

```bash
cd backend
uv run pytest tests/unit/services/test_job_runner.py tests/unit/nodes/test_execute_plan.py -v
```

Expected: tests should pass immediately since the implementations were done in Task 3. If any test fails, the error message will indicate which assertion is wrong — fix the test expectation (not the implementation).

- [ ] **Step 3: Run all unit tests to confirm no regressions**

```bash
cd backend
uv run pytest tests/unit -x -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/services/ tests/unit/nodes/test_execute_plan.py
git commit -m "test(backend): unit tests for job_runner and execute_plan with injected DAO"
```
