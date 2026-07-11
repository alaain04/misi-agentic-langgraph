# ReAct Conductor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rigid 8-node pipeline with a 5-node ReAct conductor loop where an LLM iteratively calls tools, accumulates findings, and decides when to ask the user or finalize.

**Architecture:** A `prep` subgraph clones the repo and builds context, then a `conductor → tool_runner → hitl_gate` loop runs until the conductor emits `finalize=true`, at which point `report_builder` formats the accumulated `FindingNote` list into a report. The conductor uses `with_structured_output(ConductorDecision)` for reliable routing.

**Tech Stack:** Python 3.12, LangGraph 0.2+, FastAPI, langchain-openai, asyncio, httpx (new dep for external API tools), uv

## Global Constraints

- All Python files: `uv run` for execution, `uv add` for dependencies — never `pip`
- All node functions: async (`async def`)
- External API tools (★): 10 s `asyncio.wait_for` timeout; module-level `_cache: dict` cleared per job
- No Docker calls for tools — Docker is only used inside the prep subgraph
- `repo_path` in state is always an absolute path on the host filesystem
- LLM model: `Model.GPT_5_4_MINI` for conductor; `Model.GPT_4O_MINI` for project context
- Tests: `uv run pytest <path> -v` from `apps/backend/`
- Node name strings: `"prep"`, `"conductor"`, `"tool_runner"`, `"hitl_gate"`, `"report_builder"`

---

## File Map

**Delete entirely:**
- `src/main_graph/nodes/investigation_planner.py`
- `src/main_graph/nodes/investigation_planner_service.py`
- `src/main_graph/nodes/skill_dispatcher.py`
- `src/main_graph/nodes/skill_executor.py`
- `src/main_graph/nodes/evidence_collector.py`
- `src/main_graph/nodes/evidence_correlator.py`
- `src/main_graph/nodes/finding_reviewer.py`
- `src/main_graph/skills/` (entire package)
- `src/models/evidence.py`, `src/models/hypothesis.py`, `src/models/investigation_plan.py`, `src/models/risk_finding.py`
- `src/main_graph/adapters/langchain_vector_store_adapter.py`
- `src/domain/ports/vector_store_port.py`
- `src/domain/ports/ingestion_result_port.py`
- `src/services/vector_store.py`
- `src/utils/trivy.py`
- `src/main_graph/subgraphs/discovery/nodes/generate_sbom.py`
- `src/main_graph/subgraphs/discovery/dao.py`
- `src/main_graph/subgraphs/discovery/models.py`
- `src/main_graph/utils/confidence.py`, `src/main_graph/utils/dependency_resolver.py`, `src/main_graph/utils/sbom_utils.py`
- All tests under `tests/unit/skills/`, `tests/unit/utils/test_trivy.py`, `tests/unit/utils/test_sbom_utils.py`, `tests/unit/utils/test_confidence.py`
- `tests/unit/nodes/test_evidence_correlator.py`, `test_finding_reviewer.py`, `test_investigation_planner.py`, `test_skill_dispatcher.py`
- `tests/unit/models/test_evidence.py`, `test_investigation_plan.py`, `test_risk_finding.py`
- `tests/unit/subgraphs/discovery/test_build_dependency_summary.py`

**Create:**
- `src/models/conductor.py` — ToolCall, FindingNote, ToolResult, ConductorDecision
- `src/main_graph/tools/__init__.py`
- `src/main_graph/tools/registry.py` — TOOL_REGISTRY dict mapping name → async callable
- `src/main_graph/tools/npm_cli.py` — npm subprocess tools (npm_list, npm_audit, npm_outdated)
- `src/main_graph/tools/package_files.py` — local file/JSON tools (package_json, package_lock, version_ranges, dependency_confusion, install_scripts, check_licenses, duplicate_packages, missing_dependencies, dependency_size, dependency_stats, workspace_dependencies, read_file, list_directory)
- `src/main_graph/tools/external_api.py` — ★ HTTP tools (github_advisory, osv_lookup, package_reputation, unmaintained_packages, typosquat_detection, high_risk_packages)
- `src/main_graph/nodes/conductor.py`
- `src/main_graph/nodes/tool_runner.py`
- `src/main_graph/nodes/hitl_gate.py`
- `tests/unit/models/test_conductor_models.py`
- `tests/unit/tools/test_npm_cli.py`
- `tests/unit/tools/test_package_files.py`
- `tests/unit/nodes/test_conductor.py`
- `tests/unit/nodes/test_tool_runner.py`
- `tests/unit/nodes/test_hitl_gate.py`

**Modify:**
- `src/main_graph/state.py` — replace with new MainState
- `src/main_graph/constants.py` — replace with new node constants
- `src/main_graph/config.py` — remove vector_store/sbom_dao/ingestion_daos
- `src/main_graph/graph.py` — new 5-node graph
- `src/main_graph/nodes/report_builder.py` — LLM call from FindingNote list
- `src/main_graph/subgraphs/discovery/state.py` — remove sbom fields, remove discovery_steps
- `src/main_graph/subgraphs/discovery/constants.py` — remove GENERATE_SBOM, rename BUILD_DEPENDENCY_SUMMARY
- `src/main_graph/subgraphs/discovery/graph.py` — remove generate_sbom node/edge
- `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py` → rewrite as `build_project_context.py`
- `src/main_graph/subgraphs/discovery/nodes/__init__.py` — update exports
- `src/services/job_runner.py` — new artifact tracking, autopilot, remove vector_store
- `src/api/schemas.py` — add `autopilot: bool = False` to AnalysisRequest
- `src/api/routes.py` — pass autopilot to run_analysis
- `tests/unit/models/test_state.py` — update for new MainState fields
- `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py` — remove sbom assertions

---

### Task 1: Delete removed code and stub graph

**Files:**
- Delete: all files listed in "Delete entirely" above
- Modify: `src/main_graph/graph.py`
- Modify: `src/services/job_runner.py`

**Interfaces:**
- Produces: a backend that imports cleanly (no broken references)

- [ ] **Step 1: Delete old node files**

```bash
rm apps/backend/src/main_graph/nodes/investigation_planner.py
rm apps/backend/src/main_graph/nodes/investigation_planner_service.py
rm apps/backend/src/main_graph/nodes/skill_dispatcher.py
rm apps/backend/src/main_graph/nodes/skill_executor.py
rm apps/backend/src/main_graph/nodes/evidence_collector.py
rm apps/backend/src/main_graph/nodes/evidence_correlator.py
rm apps/backend/src/main_graph/nodes/finding_reviewer.py
```

- [ ] **Step 2: Delete old skills package**

```bash
rm -rf apps/backend/src/main_graph/skills/
```

- [ ] **Step 3: Delete old models**

```bash
rm apps/backend/src/models/evidence.py
rm apps/backend/src/models/hypothesis.py
rm apps/backend/src/models/investigation_plan.py
rm apps/backend/src/models/risk_finding.py
```

- [ ] **Step 4: Delete old adapters, ports, services, utils**

```bash
rm apps/backend/src/main_graph/adapters/langchain_vector_store_adapter.py
rm apps/backend/src/domain/ports/vector_store_port.py
rm apps/backend/src/domain/ports/ingestion_result_port.py
rm apps/backend/src/services/vector_store.py
rm apps/backend/src/utils/trivy.py
rm apps/backend/src/main_graph/subgraphs/discovery/nodes/generate_sbom.py
rm apps/backend/src/main_graph/subgraphs/discovery/dao.py
rm apps/backend/src/main_graph/subgraphs/discovery/models.py
rm apps/backend/src/main_graph/utils/confidence.py
rm apps/backend/src/main_graph/utils/dependency_resolver.py
rm apps/backend/src/main_graph/utils/sbom_utils.py
```

- [ ] **Step 5: Delete stale tests**

```bash
rm -rf apps/backend/tests/unit/skills/
rm -f apps/backend/tests/unit/utils/test_trivy.py
rm -f apps/backend/tests/unit/utils/test_sbom_utils.py
rm -f apps/backend/tests/unit/utils/test_confidence.py
rm -f apps/backend/tests/unit/nodes/test_evidence_correlator.py
rm -f apps/backend/tests/unit/nodes/test_finding_reviewer.py
rm -f apps/backend/tests/unit/nodes/test_investigation_planner.py
rm -f apps/backend/tests/unit/nodes/test_skill_dispatcher.py
rm -f apps/backend/tests/unit/models/test_evidence.py
rm -f apps/backend/tests/unit/models/test_investigation_plan.py
rm -f apps/backend/tests/unit/models/test_risk_finding.py
rm -f apps/backend/tests/unit/subgraphs/discovery/test_build_dependency_summary.py
```

- [ ] **Step 6: Replace graph.py with a stub that compiles cleanly**

Replace `src/main_graph/graph.py` with:

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.state import MainState


def build_main_graph():
    builder = StateGraph(MainState)
    # Nodes will be added in Task 14
    return builder.compile(checkpointer=InMemorySaver())


main_graph = build_main_graph()
```

- [ ] **Step 7: Replace job_runner.py with a stub**

Replace `src/services/job_runner.py` with:

```python
"""Background task: run a job through the analysis pipeline."""
import logging
import shutil

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.models.job import JobStatus

logger = logging.getLogger(__name__)


def _build_config(job_id: str, dao: JobRepositoryPort) -> dict:
    container = DockerContainerAdapter()
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "container": container,
            "docker_tool": make_docker_tool(container),
        }
    }


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    autopilot: bool,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    config = _build_config(job_id, dao)
    try:
        async for _ in main_graph.astream(
            {"repo_url": repo_url, "concern": concern, "job_id": job_id,
             "autopilot": autopilot, "messages": [], "tool_results": [], "findings": []},
            config,
            stream_mode="updates",
        ):
            pass
        snapshot = await main_graph.aget_state(config)
        if snapshot.values.get("cancelled"):
            await dao.mark_cancelled(job_id)
        elif snapshot.values.get("discovery_error"):
            await dao.mark_failed(job_id, error=snapshot.values["discovery_error"])
        else:
            await dao.save_result(job_id, {"analysis_report": snapshot.values.get("analysis_report")})
    except Exception as exc:
        logger.exception("job=%s unhandled error", job_id)
        await dao.mark_failed(job_id, error=str(exc))
    finally:
        if repo_path := (await main_graph.aget_state(config)).values.get("repo_path"):
            shutil.rmtree(repo_path, ignore_errors=True)


async def resume_analysis(
    job_id: str,
    user_message: str,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.processing)
    config = _build_config(job_id, dao)
    try:
        async for _ in main_graph.astream(Command(resume=user_message), config, stream_mode="updates"):
            pass
        snapshot = await main_graph.aget_state(config)
        if snapshot.values.get("cancelled"):
            await dao.mark_cancelled(job_id)
        else:
            await dao.save_result(job_id, {"analysis_report": snapshot.values.get("analysis_report")})
    except Exception as exc:
        logger.exception("job=%s unhandled error on resume", job_id)
        await dao.mark_failed(job_id, error=str(exc))
```

- [ ] **Step 8: Verify the backend imports cleanly**

```bash
cd apps/backend && uv run python -c "from src.main_graph.graph import main_graph; print('ok')"
```

Expected: `ok` (no ImportError)

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "chore: delete old pipeline (skills, planner, dispatcher, executor, correlator, reviewer)"
```

---

### Task 2: New conductor models

**Files:**
- Create: `src/models/conductor.py`
- Create: `tests/unit/models/test_conductor_models.py`

**Interfaces:**
- Produces: `ToolCall`, `FindingNote`, `ToolResult`, `ConductorDecision` — used by conductor, tool_runner, state, report_builder

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/models/test_conductor_models.py`:

```python
import pytest
from src.models.conductor import ConductorDecision, FindingNote, ToolCall, ToolResult


def test_tool_call_requires_tool_and_args():
    tc = ToolCall(tool="npm_audit", args={"repo_path": "/tmp/repo"}, reason="check vulns")
    assert tc.tool == "npm_audit"
    assert tc.args == {"repo_path": "/tmp/repo"}


def test_finding_note_severity_values():
    for sev in ("critical", "high", "medium", "low", "info"):
        fn = FindingNote(dep_name="lodash", severity=sev, description="desc", evidence_refs=["tr-1"])
        assert fn.severity == sev


def test_tool_result_defaults():
    tr = ToolResult(id="abc", tool="npm_list", args={}, output={"deps": []}, error=None, duration_ms=42)
    assert tr.error is None
    assert tr.duration_ms == 42


def test_conductor_decision_defaults():
    d = ConductorDecision(
        tool_calls=[],
        findings=[],
        ask_user=None,
        checkpoint_message=None,
        finalize=False,
        reasoning="thinking",
    )
    assert not d.finalize
    assert d.ask_user is None
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd apps/backend && uv run pytest tests/unit/models/test_conductor_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.models.conductor'`

- [ ] **Step 3: Implement conductor models**

Create `src/models/conductor.py`:

```python
from pydantic import BaseModel


class ToolCall(BaseModel):
    tool: str
    args: dict
    reason: str


class FindingNote(BaseModel):
    dep_name: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    description: str
    evidence_refs: list[str]


class ToolResult(BaseModel):
    id: str
    tool: str
    args: dict
    output: dict
    error: str | None
    duration_ms: int


class ConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    findings: list[FindingNote]
    ask_user: str | None
    checkpoint_message: str | None
    finalize: bool
    reasoning: str
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
cd apps/backend && uv run pytest tests/unit/models/test_conductor_models.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/models/conductor.py tests/unit/models/test_conductor_models.py
git commit -m "feat: add conductor models (ToolCall, FindingNote, ToolResult, ConductorDecision)"
```

---

### Task 3: Update state, constants, and config

**Files:**
- Modify: `src/main_graph/state.py`
- Modify: `src/main_graph/constants.py`
- Modify: `src/main_graph/config.py`
- Modify: `tests/unit/models/test_state.py`

**Interfaces:**
- Produces: `MainState` TypedDict consumed by all graph nodes

- [ ] **Step 1: Rewrite state.py**

```python
import operator
from typing import Annotated, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import ProjectMetadata
from src.models.conductor import ConductorDecision, FindingNote, ToolResult


class MainState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str
    autopilot: bool

    # Prep outputs
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    project_context: NotRequired[str]
    discovery_error: NotRequired[str | None]

    # Conductor loop
    conductor_decision: NotRequired[ConductorDecision]
    tool_results: Annotated[list[ToolResult], operator.add]
    findings: Annotated[list[FindingNote], operator.add]
    conductor_iteration: NotRequired[int]
    messages: Annotated[list, add_messages]

    # Output
    analysis_report: NotRequired[dict]
    cancelled: NotRequired[bool]
```

- [ ] **Step 2: Rewrite constants.py**

```python
PREP = "prep"
CONDUCTOR = "conductor"
TOOL_RUNNER = "tool_runner"
HITL_GATE = "hitl_gate"
REPORT_BUILDER = "report_builder"
```

- [ ] **Step 3: Rewrite config.py**

```python
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from typing_extensions import TypedDict

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.job_repository_port import JobRepositoryPort


class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    container: ContainerRunPort
    docker_tool: BaseTool


def get_services(config: RunnableConfig) -> PipelineConfigurable:
    return config["configurable"]
```

- [ ] **Step 4: Update test_state.py to match new fields**

Open `tests/unit/models/test_state.py`, remove assertions about `evidence`, `investigation_plan`, `risk_findings`, `sbom_cyclonedx`, `discovery_summary`. Add assertion that `tool_results` and `findings` use `operator.add` reducer.

The file should contain:

```python
import operator
from typing import get_type_hints

from src.main_graph.state import MainState


def test_tool_results_has_add_reducer():
    hints = get_type_hints(MainState, include_extras=True)
    tool_results_hint = hints["tool_results"]
    metadata = getattr(tool_results_hint, "__metadata__", ())
    assert operator.add in metadata


def test_findings_has_add_reducer():
    hints = get_type_hints(MainState, include_extras=True)
    findings_hint = hints["findings"]
    metadata = getattr(findings_hint, "__metadata__", ())
    assert operator.add in metadata


def test_required_input_fields_present():
    hints = get_type_hints(MainState)
    for field in ("repo_url", "concern", "job_id", "autopilot"):
        assert field in hints
```

- [ ] **Step 5: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/models/test_state.py -v
```

Expected: all PASSED

- [ ] **Step 6: Verify imports still clean**

```bash
cd apps/backend && uv run python -c "from src.main_graph.graph import main_graph; print('ok')"
```

- [ ] **Step 7: Commit**

```bash
git add src/main_graph/state.py src/main_graph/constants.py src/main_graph/config.py tests/unit/models/test_state.py
git commit -m "feat: replace MainState with conductor-loop state; update constants and config"
```

---

### Task 4: Update prep subgraph

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/state.py`
- Modify: `src/main_graph/subgraphs/discovery/constants.py`
- Modify: `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py` → rewrite as build_project_context
- Modify: `src/main_graph/subgraphs/discovery/nodes/__init__.py`
- Modify: `src/main_graph/subgraphs/discovery/graph.py`

**Interfaces:**
- Produces: `project_context: str`, `repo_path`, `project_metadata`, `manifest_files`, `detected_package_manager`, `discovery_error` written to state

- [ ] **Step 1: Rewrite discovery/state.py — remove sbom fields**

```python
from typing import NotRequired
from typing_extensions import TypedDict


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int
    transitive_dependencies_count: int


class DiscoveryState(TypedDict):
    # Inputs
    job_id: str
    repo_url: str
    concern: str
    autopilot: bool

    # Set by nodes
    repo_path: NotRequired[str]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    package_manager_version: NotRequired[str]
    has_lock_file: NotRequired[bool]
    docker_image: NotRequired[str]

    # Outputs
    project_metadata: NotRequired[ProjectMetadata]
    project_context: NotRequired[str]
    discovery_error: NotRequired[str | None]
```

- [ ] **Step 2: Update discovery/constants.py**

```python
CLONE_REPO = "clone_repo"
INSPECT_REPO = "inspect_repo"
INSTALL_DEPS = "install_deps"
BUILD_PROJECT_CONTEXT = "build_project_context"
```

- [ ] **Step 3: Rewrite build_dependency_summary.py as build_project_context**

Rename the file conceptually (keep the same path for now to avoid git moves, but rename the function and update exports). Replace the entire content of `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py` with:

```python
"""Node: build_project_context — lightweight LLM summary from package.json."""
from __future__ import annotations

import json
import logging
import os

from src.main_graph.subgraphs.discovery.state import DiscoveryState, ProjectMetadata
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_SYSTEM = """\
You are analyzing a Node.js project. Given its package.json contents and the user's concern, write a concise summary (3-6 sentences, ≤ 150 words) that:
- Names the project and its stated purpose
- Lists key dependency groups most relevant to the concern
- Flags anything immediately notable (scripts, workspaces, unusual dependencies)
Output only the summary text.\
"""


def _read_package_json(repo_path: str) -> dict:
    path = os.path.join(repo_path, "package.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _count_deps(pkg: dict) -> tuple[int, int]:
    direct = len(pkg.get("dependencies", {})) + len(pkg.get("devDependencies", {}))
    return direct, 0  # transitive unknown without running npm


async def build_project_context(state: DiscoveryState) -> dict:
    error = state.get("discovery_error")
    if error:
        return {
            "project_metadata": ProjectMetadata(
                name="unknown", package_manager="unknown",
                direct_dependencies_count=0, transitive_dependencies_count=0,
            ),
            "project_context": f"Discovery failed: {error}",
        }

    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")
    pkg = _read_package_json(repo_path)
    pm = state.get("detected_package_manager", "npm")
    direct, transitive = _count_deps(pkg)

    metadata = ProjectMetadata(
        name=pkg.get("name", "unknown"),
        package_manager=pm,
        direct_dependencies_count=direct,
        transitive_dependencies_count=transitive,
    )

    pkg_summary = json.dumps(
        {k: pkg.get(k) for k in ("name", "version", "description", "scripts", "dependencies", "devDependencies", "workspaces")
         if pkg.get(k)},
        indent=2,
    )[:3000]  # cap to avoid token overflow

    response = await _llm.ainvoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Concern: {concern}\n\npackage.json:\n{pkg_summary}"},
    ])

    logger.info("build_project_context: project=%s pm=%s direct=%d", metadata["name"], pm, direct)
    return {"project_metadata": metadata, "project_context": response.content}
```

- [ ] **Step 4: Update nodes/__init__.py**

Replace `src/main_graph/subgraphs/discovery/nodes/__init__.py` with:

```python
from .build_dependency_summary import build_project_context
from .clone_repo import clone_repo
from .inspect_repo import inspect_repo
from .install_deps import install_deps

__all__ = ["clone_repo", "inspect_repo", "install_deps", "build_project_context"]
```

- [ ] **Step 5: Rewrite discovery/graph.py**

```python
from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_PROJECT_CONTEXT,
    CLONE_REPO,
    INSPECT_REPO,
    INSTALL_DEPS,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_project_context,
    clone_repo,
    inspect_repo,
    install_deps,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _route_after_clone(state: DiscoveryState) -> str:
    return BUILD_PROJECT_CONTEXT if state.get("discovery_error") else INSPECT_REPO


def _route_after_inspect(state: DiscoveryState) -> str:
    return INSTALL_DEPS if not state.get("has_lock_file") else BUILD_PROJECT_CONTEXT


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPO, clone_repo)
    builder.add_node(INSPECT_REPO, inspect_repo)
    builder.add_node(INSTALL_DEPS, install_deps)
    builder.add_node(BUILD_PROJECT_CONTEXT, build_project_context)

    builder.add_edge(START, CLONE_REPO)
    builder.add_conditional_edges(CLONE_REPO, _route_after_clone)
    builder.add_conditional_edges(INSPECT_REPO, _route_after_inspect)
    builder.add_edge(INSTALL_DEPS, BUILD_PROJECT_CONTEXT)
    builder.add_edge(BUILD_PROJECT_CONTEXT, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
```

- [ ] **Step 6: Update inspect_repo.py — remove discovery_steps**

In `src/main_graph/subgraphs/discovery/nodes/inspect_repo.py`, remove the `"discovery_steps": ["inspect_repo"]` line from the return dict.

- [ ] **Step 7: Verify imports compile**

```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.discovery.graph import discovery_subgraph; print('ok')"
```

Expected: `ok`

- [ ] **Step 8: Update discovery orchestrator test**

Open `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py`. Remove any assertions referencing `discovery_steps`, `sbom_cyclonedx`, `sbom_error`, `discovery_summary`. Ensure tests only assert on fields that still exist (`repo_path`, `project_metadata`, `project_context`, `discovery_error`).

- [ ] **Step 9: Commit**

```bash
git add src/main_graph/subgraphs/discovery/
git commit -m "feat: update prep subgraph — remove SBOM, add build_project_context"
```

---

### Task 5: Tool infrastructure

**Files:**
- Create: `src/main_graph/tools/__init__.py`
- Create: `src/main_graph/tools/registry.py`
- Create: `tests/unit/tools/__init__.py`

**Interfaces:**
- Produces: `TOOL_REGISTRY: dict[str, Callable[..., Awaitable[dict]]]` and `TOOL_DESCRIPTIONS: dict[str, str]` — imported by conductor and tool_runner

- [ ] **Step 1: Create tools package**

Create `src/main_graph/tools/__init__.py` (empty):

```python
```

Create `tests/unit/tools/__init__.py` (empty):

```python
```

- [ ] **Step 2: Create registry.py stub**

Create `src/main_graph/tools/registry.py`:

```python
"""Tool registry — maps tool names to async callables and descriptions.

Tools are populated after all tool modules are imported.
Each tool is: async (repo_path: str, **kwargs) -> dict
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# Populated by each tool module at import time via register()
TOOL_REGISTRY: dict[str, Callable[..., Awaitable[dict]]] = {}
TOOL_DESCRIPTIONS: dict[str, str] = {}


def register(name: str, description: str):
    """Decorator that registers an async tool function by name."""
    def decorator(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        TOOL_REGISTRY[name] = fn
        TOOL_DESCRIPTIONS[name] = description
        return fn
    return decorator
```

- [ ] **Step 3: Verify import works**

```bash
cd apps/backend && uv run python -c "from src.main_graph.tools.registry import TOOL_REGISTRY; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/main_graph/tools/ tests/unit/tools/
git commit -m "feat: add tool infrastructure (registry, register decorator)"
```

---

### Task 6: npm CLI tools

**Files:**
- Create: `src/main_graph/tools/npm_cli.py`
- Create: `tests/unit/tools/test_npm_cli.py`

**Interfaces:**
- Consumes: `register` from `src.main_graph.tools.registry`
- Produces: tools `npm_list`, `npm_audit`, `npm_outdated` registered in TOOL_REGISTRY

- [ ] **Step 1: Write failing tests**

Create `tests/unit/tools/test_npm_cli.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

import src.main_graph.tools.npm_cli  # trigger registration
from src.main_graph.tools.registry import TOOL_REGISTRY


@pytest.mark.asyncio
async def test_npm_list_parses_json_output():
    fake_output = '{"version": "1.0.0", "dependencies": {"lodash": {"version": "4.17.21"}}}'
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(fake_output, ""))):
        result = await TOOL_REGISTRY["npm_list"](repo_path="/tmp/repo")
    assert result["dependencies"]["lodash"]["version"] == "4.17.21"


@pytest.mark.asyncio
async def test_npm_list_returns_error_on_failure():
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(side_effect=Exception("cmd failed"))):
        result = await TOOL_REGISTRY["npm_list"](repo_path="/tmp/repo")
    assert "error" in result


@pytest.mark.asyncio
async def test_npm_audit_parses_vulnerabilities():
    fake_output = '{"vulnerabilities": {"lodash": {"severity": "high", "name": "lodash"}}, "metadata": {"vulnerabilities": {"high": 1}}}'
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(fake_output, ""))):
        result = await TOOL_REGISTRY["npm_audit"](repo_path="/tmp/repo")
    assert result["metadata"]["vulnerabilities"]["high"] == 1


@pytest.mark.asyncio
async def test_npm_outdated_parses_output():
    fake_output = '{"lodash": {"current": "4.17.20", "latest": "4.17.21", "wanted": "4.17.21"}}'
    with patch("src.main_graph.tools.npm_cli._run_npm", new=AsyncMock(return_value=(fake_output, ""))):
        result = await TOOL_REGISTRY["npm_outdated"](repo_path="/tmp/repo")
    assert "lodash" in result["outdated"]


def test_tools_are_registered():
    for name in ("npm_list", "npm_audit", "npm_outdated"):
        assert name in TOOL_REGISTRY
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_npm_cli.py -v
```

Expected: import errors

- [ ] **Step 3: Implement npm_cli.py**

Create `src/main_graph/tools/npm_cli.py`:

```python
"""npm subprocess tools: npm_list, npm_audit, npm_outdated."""
from __future__ import annotations

import asyncio
import json
import logging

from src.main_graph.tools.registry import register

logger = logging.getLogger(__name__)


async def _run_npm(args: list[str], cwd: str) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        "npm", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode(), stderr.decode()


def _safe_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


@register("npm_list", "Runs `npm list --json`; returns full dependency tree with installed versions")
async def npm_list(repo_path: str) -> dict:
    try:
        stdout, _ = await _run_npm(["list", "--json", "--all"], repo_path)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_list failed: %s", exc)
        return {"error": str(exc)}


@register("npm_audit", "Runs `npm audit --json`; returns vulnerabilities, severities, and affected packages")
async def npm_audit(repo_path: str) -> dict:
    try:
        stdout, _ = await _run_npm(["audit", "--json"], repo_path)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_audit failed: %s", exc)
        return {"error": str(exc)}


@register("npm_outdated", "Returns packages with newer versions available via `npm outdated --json`")
async def npm_outdated(repo_path: str) -> dict:
    try:
        stdout, _ = await _run_npm(["outdated", "--json"], repo_path)
        data = _safe_json(stdout)
        return {"outdated": data}
    except Exception as exc:
        logger.warning("npm_outdated failed: %s", exc)
        return {"error": str(exc)}
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_npm_cli.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/tools/npm_cli.py tests/unit/tools/test_npm_cli.py
git commit -m "feat: add npm CLI tools (npm_list, npm_audit, npm_outdated)"
```

---

### Task 7: Package file tools

**Files:**
- Create: `src/main_graph/tools/package_files.py`
- Create: `tests/unit/tools/test_package_files.py`

**Interfaces:**
- Produces: 13 tools registered: `package_json`, `package_lock`, `version_ranges`, `dependency_confusion`, `install_scripts`, `check_licenses`, `duplicate_packages`, `missing_dependencies`, `dependency_size`, `dependency_stats`, `workspace_dependencies`, `read_file`, `list_directory`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/tools/test_package_files.py`:

```python
import json
import os
import tempfile

import pytest

import src.main_graph.tools.package_files
from src.main_graph.tools.registry import TOOL_REGISTRY


@pytest.fixture
def repo(tmp_path):
    pkg = {
        "name": "my-app",
        "version": "1.0.0",
        "dependencies": {"lodash": "^4.17.21", "express": "latest"},
        "devDependencies": {"jest": "^29.0.0"},
        "scripts": {"postinstall": "node setup.js"},
        "license": "MIT",
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    return str(tmp_path)


@pytest.mark.asyncio
async def test_package_json_reads_file(repo):
    result = await TOOL_REGISTRY["package_json"](repo_path=repo)
    assert result["name"] == "my-app"
    assert "lodash" in result["dependencies"]


@pytest.mark.asyncio
async def test_version_ranges_detects_latest(repo):
    result = await TOOL_REGISTRY["version_ranges"](repo_path=repo)
    risky = [r["package"] for r in result["risky_ranges"]]
    assert "express" in risky


@pytest.mark.asyncio
async def test_install_scripts_detects_postinstall(repo):
    result = await TOOL_REGISTRY["install_scripts"](repo_path=repo)
    # postinstall is declared in the root package.json scripts
    scripts = result.get("packages_with_scripts", [])
    # root project always counted if it has lifecycle scripts
    assert any("postinstall" in str(s) for s in scripts) or result.get("note")


@pytest.mark.asyncio
async def test_read_file_returns_content(repo):
    result = await TOOL_REGISTRY["read_file"](repo_path=repo, relative_path="package.json")
    assert "my-app" in result["content"]


@pytest.mark.asyncio
async def test_read_file_missing_returns_error(repo):
    result = await TOOL_REGISTRY["read_file"](repo_path=repo, relative_path="nonexistent.txt")
    assert "error" in result


@pytest.mark.asyncio
async def test_list_directory_returns_entries(repo):
    result = await TOOL_REGISTRY["list_directory"](repo_path=repo, relative_path=".")
    assert "package.json" in result["entries"]


def test_all_package_file_tools_registered():
    expected = [
        "package_json", "package_lock", "version_ranges", "dependency_confusion",
        "install_scripts", "check_licenses", "duplicate_packages", "missing_dependencies",
        "dependency_size", "dependency_stats", "workspace_dependencies",
        "read_file", "list_directory",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"{name} not in TOOL_REGISTRY"
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_package_files.py -v
```

Expected: import errors

- [ ] **Step 3: Implement package_files.py**

Create `src/main_graph/tools/package_files.py`:

```python
"""Local file and JSON analysis tools."""
from __future__ import annotations

import json
import logging
import os

from src.main_graph.tools.registry import register

logger = logging.getLogger(__name__)

_WIDE_RANGE_PATTERNS = ("*", "latest", "next", "x", "")


def _load_pkg(repo_path: str) -> dict:
    try:
        with open(os.path.join(repo_path, "package.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def _all_deps(pkg: dict) -> dict[str, str]:
    return {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
        **pkg.get("optionalDependencies", {}),
        **pkg.get("peerDependencies", {}),
    }


def _is_wide_range(spec: str) -> bool:
    s = spec.strip()
    return s in _WIDE_RANGE_PATTERNS or s.startswith(">=") or (s.startswith("^") and s[1:2] == "0")


@register("package_json", "Parses package.json; returns declared dependencies, scripts, engines, workspaces")
async def package_json(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    if not pkg:
        return {"error": "package.json not found or invalid"}
    return pkg


@register("package_lock", "Parses package-lock.json or lockfile; returns resolved versions and integrity hashes")
async def package_lock(repo_path: str) -> dict:
    for name in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
        path = os.path.join(repo_path, name)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    content = f.read()
                if name == "package-lock.json":
                    return {"lockfile": name, "data": json.loads(content)}
                return {"lockfile": name, "raw_size_bytes": len(content), "note": "non-JSON lockfile, use npm_list for resolved versions"}
            except Exception as exc:
                return {"error": str(exc)}
    return {"error": "no lockfile found"}


@register("version_ranges", "Detects broad version ranges (*, latest, wide ^ or >=) in package.json")
async def version_ranges(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = _all_deps(pkg)
    risky = [{"package": name, "range": spec} for name, spec in deps.items() if _is_wide_range(spec)]
    return {"risky_ranges": risky, "total_checked": len(deps)}


@register("dependency_confusion", "Detects internal/private package names that may be vulnerable to dependency confusion")
async def dependency_confusion(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = _all_deps(pkg)
    # Heuristic: names with org scope (@company/) or containing 'internal', 'private', 'local'
    suspicious = []
    for name in deps:
        if any(kw in name.lower() for kw in ("internal", "private", "local", "corp", "intranet")):
            suspicious.append({"package": name, "reason": "name suggests private/internal package"})
    return {"suspicious_packages": suspicious, "note": "Verify these exist on npm registry"}


@register("install_scripts", "Detects packages with lifecycle scripts (preinstall, install, postinstall)")
async def install_scripts(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    scripts = pkg.get("scripts", {})
    lifecycle = ["preinstall", "install", "postinstall", "prepare", "prepack", "postpack"]
    found = [s for s in lifecycle if s in scripts]
    packages_with_scripts = []
    if found:
        packages_with_scripts.append({"package": pkg.get("name", "root"), "scripts": found})
    # Check node_modules for other packages with scripts (best-effort)
    nm_path = os.path.join(repo_path, "node_modules")
    if os.path.isdir(nm_path):
        for entry in os.listdir(nm_path)[:100]:  # limit scan
            pkg_path = os.path.join(nm_path, entry, "package.json")
            try:
                with open(pkg_path) as f:
                    dep_pkg = json.load(f)
                dep_scripts = dep_pkg.get("scripts", {})
                dep_found = [s for s in lifecycle if s in dep_scripts]
                if dep_found:
                    packages_with_scripts.append({"package": entry, "scripts": dep_found})
            except Exception:
                pass
    return {"packages_with_scripts": packages_with_scripts}


@register("check_licenses", "Collects licenses for all dependencies and flags non-permissive licenses")
async def check_licenses(repo_path: str) -> dict:
    nm_path = os.path.join(repo_path, "node_modules")
    permissive = {"mit", "isc", "bsd-2-clause", "bsd-3-clause", "apache-2.0", "cc0-1.0", "0bsd", "unlicensed"}
    results = []
    if os.path.isdir(nm_path):
        for entry in os.listdir(nm_path)[:200]:
            pkg_path = os.path.join(nm_path, entry, "package.json")
            try:
                with open(pkg_path) as f:
                    dep_pkg = json.load(f)
                lic = dep_pkg.get("license", "UNKNOWN")
                flagged = str(lic).lower() not in permissive
                results.append({"package": entry, "license": lic, "flagged": flagged})
            except Exception:
                pass
    flagged = [r for r in results if r["flagged"]]
    return {"licenses": results, "flagged_count": len(flagged), "flagged": flagged}


@register("duplicate_packages", "Finds multiple installed versions of the same package")
async def duplicate_packages(repo_path: str) -> dict:
    nm_path = os.path.join(repo_path, "node_modules")
    seen: dict[str, list[str]] = {}
    if os.path.isdir(nm_path):
        for entry in os.listdir(nm_path):
            pkg_path = os.path.join(nm_path, entry, "package.json")
            try:
                with open(pkg_path) as f:
                    dep_pkg = json.load(f)
                name = dep_pkg.get("name", entry)
                version = dep_pkg.get("version", "?")
                seen.setdefault(name, []).append(version)
            except Exception:
                pass
    duplicates = {name: versions for name, versions in seen.items() if len(versions) > 1}
    return {"duplicates": duplicates, "duplicate_count": len(duplicates)}


@register("missing_dependencies", "Finds packages imported in source files but absent from package.json")
async def missing_dependencies(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    declared = set(_all_deps(pkg).keys())
    # Simple heuristic: scan JS/TS files for require/import statements
    imported: set[str] = set()
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "dist", "build")]
        for fname in files:
            if fname.endswith((".js", ".ts", ".jsx", ".tsx")):
                try:
                    content = open(os.path.join(root, fname)).read()
                    for line in content.splitlines():
                        line = line.strip()
                        for prefix in ("require('", 'require("', "from '", 'from "'):
                            if prefix in line:
                                idx = line.index(prefix) + len(prefix)
                                end = line.find(line[idx - 1], idx)
                                if end > idx:
                                    spec = line[idx:end].split("/")[0]
                                    if spec and not spec.startswith(".") and not spec.startswith("node:"):
                                        imported.add(spec)
                except Exception:
                    pass
    missing = [m for m in imported if m not in declared and not m.startswith("@types/")]
    return {"missing": missing, "checked_declared": len(declared)}


@register("dependency_size", "Estimates install size and identifies large dependencies")
async def dependency_size(repo_path: str) -> dict:
    nm_path = os.path.join(repo_path, "node_modules")
    if not os.path.isdir(nm_path):
        return {"error": "node_modules not found — run install first"}
    sizes: list[dict] = []
    for entry in os.listdir(nm_path):
        entry_path = os.path.join(nm_path, entry)
        if not os.path.isdir(entry_path):
            continue
        total = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(entry_path)
            for f in files
        )
        sizes.append({"package": entry, "size_bytes": total})
    sizes.sort(key=lambda x: x["size_bytes"], reverse=True)
    total_bytes = sum(s["size_bytes"] for s in sizes)
    return {"total_bytes": total_bytes, "top_10_by_size": sizes[:10], "package_count": len(sizes)}


@register("dependency_stats", "Reports total, direct, transitive, dev, optional, and peer dependency counts")
async def dependency_stats(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    return {
        "direct": len(pkg.get("dependencies", {})),
        "dev": len(pkg.get("devDependencies", {})),
        "optional": len(pkg.get("optionalDependencies", {})),
        "peer": len(pkg.get("peerDependencies", {})),
        "total_declared": len(_all_deps(pkg)),
    }


@register("workspace_dependencies", "Lists dependencies per workspace for monorepo projects")
async def workspace_dependencies(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    workspaces = pkg.get("workspaces", [])
    if not workspaces:
        return {"workspaces": [], "note": "Not a monorepo or workspaces not declared"}
    results = []
    import glob
    for pattern in (workspaces if isinstance(workspaces, list) else workspaces.get("packages", [])):
        for ws_path in glob.glob(os.path.join(repo_path, pattern)):
            ws_pkg_path = os.path.join(ws_path, "package.json")
            try:
                with open(ws_pkg_path) as f:
                    ws_pkg = json.load(f)
                results.append({
                    "workspace": os.path.relpath(ws_path, repo_path),
                    "name": ws_pkg.get("name"),
                    "dependencies": list(ws_pkg.get("dependencies", {}).keys()),
                })
            except Exception:
                pass
    return {"workspaces": results}


@register("read_file", "Reads a specific file from the cloned repo")
async def read_file(repo_path: str, relative_path: str) -> dict:
    full_path = os.path.normpath(os.path.join(repo_path, relative_path))
    if not full_path.startswith(os.path.normpath(repo_path)):
        return {"error": "path traversal not allowed"}
    try:
        with open(full_path) as f:
            content = f.read(50_000)  # cap at 50 kB
        return {"content": content, "truncated": os.path.getsize(full_path) > 50_000}
    except FileNotFoundError:
        return {"error": f"{relative_path} not found"}
    except Exception as exc:
        return {"error": str(exc)}


@register("list_directory", "Lists files at a path in the cloned repo")
async def list_directory(repo_path: str, relative_path: str = ".") -> dict:
    full_path = os.path.normpath(os.path.join(repo_path, relative_path))
    if not full_path.startswith(os.path.normpath(repo_path)):
        return {"error": "path traversal not allowed"}
    try:
        entries = os.listdir(full_path)
        return {"entries": sorted(entries), "count": len(entries)}
    except FileNotFoundError:
        return {"error": f"{relative_path} not found"}
    except Exception as exc:
        return {"error": str(exc)}
```

- [ ] **Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/tools/test_package_files.py -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/tools/package_files.py tests/unit/tools/test_package_files.py
git commit -m "feat: add package file analysis tools (13 tools)"
```

---

### Task 8: External API tools

**Files:**
- Create: `src/main_graph/tools/external_api.py`

**Interfaces:**
- Consumes: `httpx` (add with `uv add httpx`)
- Produces: 6 tools registered: `github_advisory`, `osv_lookup`, `package_reputation`, `unmaintained_packages`, `typosquat_detection`, `high_risk_packages`

- [ ] **Step 1: Add httpx dependency**

```bash
cd apps/backend && uv add httpx
```

- [ ] **Step 2: Implement external_api.py**

Create `src/main_graph/tools/external_api.py`:

```python
"""External API tools (★) — all have 10s timeout and session-level cache."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime, timedelta

import httpx

from src.main_graph.tools.package_files import _load_pkg, _all_deps
from src.main_graph.tools.registry import register

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_cache: dict[str, dict] = {}


def clear_cache() -> None:
    _cache.clear()


def _cached(key: str, fn):
    async def wrapper():
        if key not in _cache:
            _cache[key] = await fn()
        return _cache[key]
    return wrapper()


async def _get(url: str, headers: dict | None = None, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, headers=headers or {}, params=params or {})
        r.raise_for_status()
        return r.json()


async def _npm_metadata(package_name: str) -> dict:
    key = f"npm:{package_name}"
    if key in _cache:
        return _cache[key]
    try:
        data = await asyncio.wait_for(_get(f"https://registry.npmjs.org/{package_name}"), _TIMEOUT)
        _cache[key] = data
        return data
    except Exception as exc:
        return {"error": str(exc)}


@register("github_advisory", "Queries GitHub Advisory Database (GraphQL) for known vulnerabilities in a package")
async def github_advisory(package_name: str, ecosystem: str = "NPM") -> dict:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        return {"error": "GITHUB_TOKEN not set", "advisories": []}
    query = """
    query($ecosystem: SecurityAdvisoryEcosystem!, $package: String!) {
      securityVulnerabilities(ecosystem: $ecosystem, package: $package, first: 20) {
        nodes {
          severity
          updatedAt
          advisory { summary ghsaId permalink publishedAt }
          vulnerableVersionRange
          firstPatchedVersion { identifier }
        }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                "https://api.github.com/graphql",
                json={"query": query, "variables": {"ecosystem": ecosystem, "package": package_name}},
                headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        nodes = data.get("data", {}).get("securityVulnerabilities", {}).get("nodes", [])
        return {"package": package_name, "advisories": nodes, "count": len(nodes)}
    except Exception as exc:
        return {"error": str(exc), "advisories": []}


@register("osv_lookup", "Queries OSV.dev for vulnerability records for a package version")
async def osv_lookup(package_name: str, version: str = "", ecosystem: str = "npm") -> dict:
    payload = {"package": {"name": package_name, "ecosystem": ecosystem}}
    if version:
        payload["version"] = version
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post("https://api.osv.dev/v1/query", json=payload)
            r.raise_for_status()
            data = r.json()
        vulns = data.get("vulns", [])
        return {"package": package_name, "version": version, "vulnerabilities": vulns, "count": len(vulns)}
    except Exception as exc:
        return {"error": str(exc), "vulnerabilities": []}


@register("package_reputation", "Reports package age, maintainers, release cadence, and popularity via npm registry")
async def package_reputation(package_name: str) -> dict:
    meta = await _npm_metadata(package_name)
    if "error" in meta:
        return meta
    time_data = meta.get("time", {})
    versions = list(time_data.keys())
    created = time_data.get("created", "")
    modified = time_data.get("modified", "")
    maintainers = meta.get("maintainers", [])
    latest_ver = meta.get("dist-tags", {}).get("latest", "")
    weekly_downloads = meta.get("downloads", {}).get("last-week", None)
    return {
        "package": package_name,
        "created": created,
        "last_modified": modified,
        "version_count": len([v for v in versions if v not in ("created", "modified")]),
        "latest_version": latest_ver,
        "maintainer_count": len(maintainers),
        "maintainers": [m.get("name") for m in maintainers],
        "weekly_downloads": weekly_downloads,
    }


@register("unmaintained_packages", "Flags packages with no releases for 12+ months based on npm registry data")
async def unmaintained_packages(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    cutoff = datetime.now(UTC) - timedelta(days=365)
    flagged = []
    for dep in deps[:30]:  # limit to avoid rate limiting
        meta = await _npm_metadata(dep)
        if "error" in meta:
            continue
        modified_str = meta.get("time", {}).get("modified", "")
        try:
            modified = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
            if modified < cutoff:
                flagged.append({"package": dep, "last_modified": modified_str})
        except Exception:
            pass
    return {"unmaintained": flagged, "checked": min(len(deps), 30)}


_POPULAR_PACKAGES = {
    "lodash", "express", "react", "vue", "angular", "webpack", "babel", "eslint",
    "prettier", "jest", "mocha", "axios", "moment", "dayjs", "uuid", "chalk",
    "commander", "yargs", "dotenv", "cors", "helmet", "passport", "sequelize",
    "mongoose", "redis", "bull", "socket.io", "ws", "http-proxy", "node-fetch",
}


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


@register("typosquat_detection", "Detects package names similar to popular packages (edit distance ≤ 2)")
async def typosquat_detection(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    flagged = []
    for dep in deps:
        dep_clean = dep.lstrip("@").split("/")[-1]
        for popular in _POPULAR_PACKAGES:
            if dep_clean != popular and _edit_distance(dep_clean, popular) <= 2:
                flagged.append({"package": dep, "similar_to": popular, "edit_distance": _edit_distance(dep_clean, popular)})
                break
    return {"potential_typosquats": flagged, "checked": len(deps)}


@register("high_risk_packages", "Flags packages with unusual risk characteristics (new, single-maintainer, abandoned)")
async def high_risk_packages(repo_path: str) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    cutoff_new = datetime.now(UTC) - timedelta(days=90)
    cutoff_abandoned = datetime.now(UTC) - timedelta(days=730)
    flagged = []
    for dep in deps[:30]:
        meta = await _npm_metadata(dep)
        if "error" in meta:
            continue
        time_data = meta.get("time", {})
        created_str = time_data.get("created", "")
        modified_str = time_data.get("modified", "")
        maintainer_count = len(meta.get("maintainers", []))
        reasons = []
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created > cutoff_new:
                reasons.append("very new package (<90 days)")
        except Exception:
            pass
        try:
            modified = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
            if modified < cutoff_abandoned:
                reasons.append("abandoned (>2 years no release)")
        except Exception:
            pass
        if maintainer_count == 1:
            reasons.append("single maintainer")
        if reasons:
            flagged.append({"package": dep, "reasons": reasons})
    return {"high_risk": flagged, "checked": min(len(deps), 30)}
```

- [ ] **Step 3: Verify registration**

```bash
cd apps/backend && uv run python -c "
import src.main_graph.tools.external_api as m
from src.main_graph.tools.registry import TOOL_REGISTRY
for name in ('github_advisory', 'osv_lookup', 'package_reputation', 'unmaintained_packages', 'typosquat_detection', 'high_risk_packages'):
    assert name in TOOL_REGISTRY, f'missing: {name}'
print('all 6 external tools registered')
"
```

Expected: `all 6 external tools registered`

- [ ] **Step 4: Commit**

```bash
git add src/main_graph/tools/external_api.py
git commit -m "feat: add external API tools — github_advisory, osv_lookup, package_reputation, unmaintained_packages, typosquat_detection, high_risk_packages"
```

---

### Task 9: Conductor node

**Files:**
- Create: `src/main_graph/nodes/conductor.py`
- Create: `tests/unit/nodes/test_conductor.py`

**Interfaces:**
- Consumes: `ConductorDecision` from `src.models.conductor`, `TOOL_DESCRIPTIONS` from `src.main_graph.tools.registry`, `MainState`, `get_services`
- Produces: state update `{"conductor_decision": ConductorDecision, "conductor_iteration": int, "findings": list[FindingNote]}`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/nodes/test_conductor.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage

from src.main_graph.nodes.conductor import conductor
from src.models.conductor import ConductorDecision, FindingNote, ToolCall, ToolResult


def _make_state(**kwargs):
    defaults = {
        "repo_url": "https://github.com/test/repo",
        "concern": "security vulnerabilities",
        "job_id": "job-1",
        "autopilot": False,
        "project_context": "A Node.js API with lodash and express",
        "detected_package_manager": "npm",
        "tool_results": [],
        "findings": [],
        "conductor_iteration": 0,
        "messages": [],
    }
    return {**defaults, **kwargs}


@pytest.mark.asyncio
async def test_conductor_increments_iteration():
    decision = ConductorDecision(
        tool_calls=[ToolCall(tool="npm_audit", args={}, reason="check")],
        findings=[], ask_user=None, checkpoint_message=None, finalize=False, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        result = await conductor(_make_state(), config={"configurable": {}})
    assert result["conductor_iteration"] == 1


@pytest.mark.asyncio
async def test_conductor_forces_finalize_at_max_iterations():
    decision = ConductorDecision(
        tool_calls=[ToolCall(tool="npm_audit", args={}, reason="check")],
        findings=[], ask_user=None, checkpoint_message=None, finalize=False, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        state = _make_state(conductor_iteration=9)
        result = await conductor(state, config={"configurable": {}})
    assert result["conductor_decision"].finalize is True


@pytest.mark.asyncio
async def test_conductor_suppresses_ask_user_in_autopilot():
    decision = ConductorDecision(
        tool_calls=[], findings=[], ask_user="can you clarify?",
        checkpoint_message=None, finalize=False, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        result = await conductor(_make_state(autopilot=True), config={"configurable": {}})
    assert result["conductor_decision"].ask_user is None
    assert result["conductor_decision"].checkpoint_message is None


@pytest.mark.asyncio
async def test_conductor_accumulates_findings():
    new_finding = FindingNote(dep_name="lodash", severity="high", description="vuln", evidence_refs=[])
    decision = ConductorDecision(
        tool_calls=[], findings=[new_finding], ask_user=None,
        checkpoint_message=None, finalize=True, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        result = await conductor(_make_state(), config={"configurable": {}})
    assert len(result["findings"]) == 1
    assert result["findings"][0].dep_name == "lodash"
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_conductor.py -v
```

Expected: import errors

- [ ] **Step 3: Implement conductor.py**

Create `src/main_graph/nodes/conductor.py`:

```python
"""Conductor node — ReAct loop brain."""
from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.state import MainState
import src.main_graph.tools.npm_cli  # noqa: F401 — trigger registration
import src.main_graph.tools.package_files  # noqa: F401
import src.main_graph.tools.external_api  # noqa: F401
from src.main_graph.tools.registry import TOOL_DESCRIPTIONS
from src.models.conductor import ConductorDecision
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 10
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a dependency risk investigator. You run tools to investigate a Node.js project and accumulate findings.

Each iteration you MUST output a ConductorDecision with exactly one primary action:
1. finalize=true — you have enough findings to write the report (highest priority)
2. ask_user or checkpoint_message set — you need user input before continuing
3. tool_calls non-empty — run these tools in parallel and observe results next iteration

Rules:
- Never repeat a tool call with identical arguments.
- Emit FindingNote entries for every risk you observe in tool results.
- In autopilot mode, never set ask_user or checkpoint_message.
- After 10 iterations, you MUST finalize regardless of confidence.

Available tools:
{tool_descriptions}
"""


def _format_tool_descriptions() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in TOOL_DESCRIPTIONS.items())


def _format_tool_results(tool_results: list) -> str:
    if not tool_results:
        return "No tool results yet."
    parts = []
    for tr in tool_results[-20:]:  # show last 20 to avoid context overflow
        output_str = json.dumps(tr.output, indent=2)[:2000]
        parts.append(f"[{tr.id}] {tr.tool}({tr.args}) → {output_str}")
    return "\n\n".join(parts)


def _format_findings(findings: list) -> str:
    if not findings:
        return "No findings yet."
    return "\n".join(
        f"- [{f.severity.upper()}] {f.dep_name}: {f.description}"
        for f in findings
    )


async def conductor(state: MainState, config: RunnableConfig) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1

    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Project context:\n{state.get('project_context', '')}\n\n"
        f"Package manager: {state.get('detected_package_manager', 'unknown')}\n\n"
        f"Tool results so far:\n{_format_tool_results(state.get('tool_results') or [])}\n\n"
        f"Findings accumulated:\n{_format_findings(state.get('findings') or [])}\n\n"
        f"Conversation history: {len(state.get('messages') or [])} messages\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )
    if state.get("autopilot"):
        user_prompt += "\n\nAUTOPILOT MODE: do not set ask_user or checkpoint_message."

    system = _SYSTEM.format(tool_descriptions=_format_tool_descriptions())

    structured_llm = _llm.with_structured_output(ConductorDecision)
    decision: ConductorDecision = await structured_llm.ainvoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ])

    # Enforce max iteration guard
    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    # Enforce autopilot
    if state.get("autopilot"):
        decision = decision.model_copy(update={"ask_user": None, "checkpoint_message": None})

    logger.info(
        "conductor: iteration=%d finalize=%s tools=%d findings=%d ask_user=%s",
        iteration, decision.finalize, len(decision.tool_calls), len(decision.findings),
        bool(decision.ask_user),
    )

    return {
        "conductor_iteration": iteration,
        "conductor_decision": decision,
        "findings": decision.findings,  # accumulated via operator.add
    }
```

- [ ] **Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_conductor.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/conductor.py tests/unit/nodes/test_conductor.py
git commit -m "feat: add conductor node with structured LLM output and ReAct loop logic"
```

---

### Task 10: Tool runner node

**Files:**
- Create: `src/main_graph/nodes/tool_runner.py`
- Create: `tests/unit/nodes/test_tool_runner.py`

**Interfaces:**
- Consumes: `state["conductor_decision"].tool_calls`, `state["repo_path"]`, `TOOL_REGISTRY`
- Produces: state update `{"tool_results": list[ToolResult]}`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/nodes/test_tool_runner.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.nodes.tool_runner import tool_runner
from src.models.conductor import ConductorDecision, ToolCall, ToolResult


def _make_state(tool_calls: list[ToolCall], repo_path: str = "/tmp/repo"):
    decision = ConductorDecision(
        tool_calls=tool_calls, findings=[], ask_user=None,
        checkpoint_message=None, finalize=False, reasoning="r",
    )
    return {
        "repo_url": "https://github.com/test/repo",
        "concern": "security",
        "job_id": "j1",
        "autopilot": False,
        "repo_path": repo_path,
        "tool_results": [],
        "findings": [],
        "conductor_iteration": 1,
        "messages": [],
        "conductor_decision": decision,
    }


@pytest.mark.asyncio
async def test_tool_runner_executes_registered_tool():
    fake_output = {"deps": {"lodash": "4.17.21"}}
    tc = ToolCall(tool="npm_list", args={}, reason="check deps")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"npm_list": AsyncMock(return_value=fake_output)}):
        result = await tool_runner(_make_state([tc]), config={})
    assert len(result["tool_results"]) == 1
    tr: ToolResult = result["tool_results"][0]
    assert tr.tool == "npm_list"
    assert tr.output == fake_output
    assert tr.error is None


@pytest.mark.asyncio
async def test_tool_runner_captures_error_for_unknown_tool():
    tc = ToolCall(tool="nonexistent_tool", args={}, reason="test")
    result = await tool_runner(_make_state([tc]), config={})
    assert len(result["tool_results"]) == 1
    tr: ToolResult = result["tool_results"][0]
    assert tr.error is not None
    assert "not found" in tr.error


@pytest.mark.asyncio
async def test_tool_runner_runs_multiple_tools_in_parallel():
    import asyncio
    call_times = []

    async def slow_tool(**_kwargs):
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.05)
        return {"ok": True}

    tcs = [
        ToolCall(tool="tool_a", args={}, reason="a"),
        ToolCall(tool="tool_b", args={}, reason="b"),
    ]
    fake_registry = {"tool_a": slow_tool, "tool_b": slow_tool}
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", fake_registry):
        import time
        start = time.monotonic()
        result = await tool_runner(_make_state(tcs), config={})
        elapsed = time.monotonic() - start
    assert len(result["tool_results"]) == 2
    # Parallel execution should be ~50ms, not ~100ms
    assert elapsed < 0.08, f"tools ran sequentially: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_tool_runner_sets_duration_ms():
    tc = ToolCall(tool="npm_list", args={}, reason="check")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"npm_list": AsyncMock(return_value={})}):
        result = await tool_runner(_make_state([tc]), config={})
    assert result["tool_results"][0].duration_ms >= 0
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_tool_runner.py -v
```

- [ ] **Step 3: Implement tool_runner.py**

Create `src/main_graph/nodes/tool_runner.py`:

```python
"""Tool runner node — executes conductor tool calls in parallel."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from langchain_core.runnables import RunnableConfig

from src.main_graph.state import MainState
from src.main_graph.tools.registry import TOOL_REGISTRY
from src.models.conductor import ToolCall, ToolResult

logger = logging.getLogger(__name__)


async def _run_tool(tc: ToolCall, repo_path: str) -> ToolResult:
    start = time.monotonic()
    fn = TOOL_REGISTRY.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=f"tool '{tc.tool}' not found in registry",
            duration_ms=0,
        )
    try:
        output = await fn(repo_path=repo_path, **tc.args)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output=output, error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("tool_runner: tool=%s failed: %s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


async def tool_runner(state: MainState, config: RunnableConfig) -> dict:
    decision = state.get("conductor_decision")
    if decision is None or not decision.tool_calls:
        return {"tool_results": []}

    repo_path = state.get("repo_path", "")
    tool_calls = decision.tool_calls

    logger.info("tool_runner: executing %d tools in parallel", len(tool_calls))
    results = await asyncio.gather(*[_run_tool(tc, repo_path) for tc in tool_calls])

    for tr in results:
        if tr.error:
            logger.warning("tool_runner: tool=%s error=%s", tr.tool, tr.error)
        else:
            logger.info("tool_runner: tool=%s duration_ms=%d", tr.tool, tr.duration_ms)

    return {"tool_results": list(results)}
```

- [ ] **Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_tool_runner.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/tool_runner.py tests/unit/nodes/test_tool_runner.py
git commit -m "feat: add tool_runner node — asyncio.gather parallel tool execution"
```

---

### Task 11: HITL gate node

**Files:**
- Create: `src/main_graph/nodes/hitl_gate.py`
- Create: `tests/unit/nodes/test_hitl_gate.py`

**Interfaces:**
- Consumes: `state["conductor_decision"]`, `state["autopilot"]`, `state["job_id"]`, `get_services(config)["job_repo"]`
- Produces: state update `{"messages": [...]}` after user reply, or pass-through in autopilot

- [ ] **Step 1: Write failing tests**

Create `tests/unit/nodes/test_hitl_gate.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.hitl_gate import hitl_gate
from src.models.conductor import ConductorDecision, ToolCall


def _make_state(autopilot=False, ask_user=None, checkpoint_message=None):
    decision = ConductorDecision(
        tool_calls=[], findings=[], ask_user=ask_user,
        checkpoint_message=checkpoint_message, finalize=False, reasoning="r",
    )
    return {
        "repo_url": "https://github.com/test/repo",
        "concern": "security",
        "job_id": "j1",
        "autopilot": autopilot,
        "tool_results": [],
        "findings": [],
        "conductor_iteration": 1,
        "messages": [],
        "conductor_decision": decision,
    }


def _make_config(dao=None):
    mock_dao = dao or AsyncMock()
    return {"configurable": {"job_repo": mock_dao}}


@pytest.mark.asyncio
async def test_hitl_gate_passthrough_in_autopilot():
    state = _make_state(autopilot=True, ask_user="what should I do?")
    # Should return without calling interrupt()
    with patch("src.main_graph.nodes.hitl_gate.interrupt") as mock_interrupt:
        result = await hitl_gate(state, config=_make_config())
    mock_interrupt.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_hitl_gate_passthrough_when_no_question():
    state = _make_state(autopilot=False, ask_user=None, checkpoint_message=None)
    with patch("src.main_graph.nodes.hitl_gate.interrupt") as mock_interrupt:
        result = await hitl_gate(state, config=_make_config())
    mock_interrupt.assert_not_called()


@pytest.mark.asyncio
async def test_hitl_gate_calls_interrupt_for_ask_user():
    state = _make_state(autopilot=False, ask_user="Can you clarify the concern?")
    mock_dao = AsyncMock()
    # interrupt() raises GraphInterrupt in LangGraph; we simulate by patching
    with patch("src.main_graph.nodes.hitl_gate.interrupt", return_value="user reply") as mock_interrupt:
        with patch("src.main_graph.nodes.hitl_gate.get_services", return_value={"job_repo": mock_dao}):
            result = await hitl_gate(state, config=_make_config(mock_dao))
    mock_interrupt.assert_called_once()
    assert "messages" in result
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_hitl_gate.py -v
```

- [ ] **Step 3: Implement hitl_gate.py**

Create `src/main_graph/nodes/hitl_gate.py`:

```python
"""HITL gate node — pause for user input or pass through in autopilot."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from src.main_graph.config import get_services
from src.main_graph.constants import HITL_GATE
from src.main_graph.state import MainState

logger = logging.getLogger(__name__)


async def hitl_gate(state: MainState, config: RunnableConfig) -> dict:
    decision = state.get("conductor_decision")
    if decision is None:
        return {}

    autopilot = state.get("autopilot", False)
    question = decision.ask_user or decision.checkpoint_message

    if not question or autopilot:
        return {}

    job_id = state["job_id"]
    svc = get_services(config)
    dao = svc["job_repo"]

    created_at = datetime.now(UTC).isoformat()
    await dao.push_artifact_message(job_id, HITL_GATE, {
        "role": "assistant",
        "content": question,
        "created_at": created_at,
        "type": "ask_user" if decision.ask_user else "checkpoint",
    })

    user_reply: str = interrupt({"question": question, "type": "ask_user" if decision.ask_user else "checkpoint"})

    await dao.push_artifact_message(job_id, HITL_GATE, {
        "role": "human",
        "content": user_reply,
        "created_at": datetime.now(UTC).isoformat(),
    })

    logger.info("hitl_gate: job=%s resumed with user reply", job_id)
    return {"messages": [AIMessage(content=question), HumanMessage(content=user_reply)]}
```

- [ ] **Step 4: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_hitl_gate.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/hitl_gate.py tests/unit/nodes/test_hitl_gate.py
git commit -m "feat: add hitl_gate node — interrupt() for ask_user/checkpoint, pass-through in autopilot"
```

---

### Task 12: Updated report_builder

**Files:**
- Modify: `src/main_graph/nodes/report_builder.py`
- Modify: `tests/unit/nodes/test_report_builder.py`

**Interfaces:**
- Consumes: `state["findings"]: list[FindingNote]`, `state["concern"]`, `state["messages"]`
- Produces: state update `{"analysis_report": dict}`

- [ ] **Step 1: Rewrite report_builder.py**

Replace `src/main_graph/nodes/report_builder.py` with:

```python
"""Report builder — single LLM call that formats accumulated FindingNote entries into a report."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from src.main_graph.state import MainState
from src.models.conductor import FindingNote
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_SYSTEM = """\
You are a technical report writer. Given structured investigation findings, produce a JSON analysis report.

Output ONLY valid JSON matching this exact shape:
{
  "executive_summary": "<2-4 sentence summary of overall risk>",
  "overall_risk_level": "<critical|high|medium|low|none>",
  "findings": [
    {
      "dep_name": "<package name>",
      "severity": "<critical|high|medium|low|info>",
      "description": "<concise description>",
      "recommendation": "<actionable fix>",
      "evidence_refs": ["<tool result id>"]
    }
  ],
  "recommendations": ["<deduplicated list of top recommendations>"]
}
"""


def _format_findings(findings: list[FindingNote]) -> str:
    return json.dumps(
        [{"dep_name": f.dep_name, "severity": f.severity, "description": f.description, "evidence_refs": f.evidence_refs}
         for f in findings],
        indent=2,
    )


def _overall_risk(findings: list[FindingNote]) -> str:
    if not findings:
        return "none"
    return max(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0)).severity


async def report_builder(state: MainState) -> dict:
    findings = state.get("findings") or []
    concern = state.get("concern", "")

    if not findings:
        report = {
            "concern": concern,
            "generated_at": datetime.now(UTC).isoformat(),
            "overall_risk_level": "none",
            "executive_summary": "No significant findings were identified during the investigation.",
            "findings": [],
            "recommendations": [],
        }
        return {"analysis_report": report}

    sorted_findings = sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0), reverse=True)

    response = await _llm.ainvoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Concern: {concern}\n\nFindings:\n{_format_findings(sorted_findings)}"},
    ])

    try:
        import json as _json
        from src.utils.llm import parse_llm_json
        report_data = parse_llm_json(response.content or "")
    except Exception:
        report_data = {"executive_summary": response.content, "findings": [], "recommendations": []}

    report = {
        "concern": concern,
        "generated_at": datetime.now(UTC).isoformat(),
        "overall_risk_level": _overall_risk(findings),
        **report_data,
    }

    logger.info("report_builder: findings=%d overall_risk=%s", len(findings), report["overall_risk_level"])
    return {"analysis_report": report}
```

- [ ] **Step 2: Rewrite test_report_builder.py**

Replace `tests/unit/nodes/test_report_builder.py` with:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.report_builder import report_builder
from src.models.conductor import FindingNote


def _make_state(findings=None, concern="security"):
    return {
        "repo_url": "https://github.com/test/repo",
        "concern": concern,
        "job_id": "j1",
        "autopilot": False,
        "tool_results": [],
        "findings": findings or [],
        "conductor_iteration": 3,
        "messages": [],
    }


@pytest.mark.asyncio
async def test_report_builder_returns_none_risk_when_no_findings():
    result = await report_builder(_make_state(findings=[]))
    assert result["analysis_report"]["overall_risk_level"] == "none"


@pytest.mark.asyncio
async def test_report_builder_calls_llm_with_findings():
    findings = [
        FindingNote(dep_name="lodash", severity="high", description="vuln", evidence_refs=["tr-1"]),
        FindingNote(dep_name="express", severity="medium", description="outdated", evidence_refs=[]),
    ]
    mock_response = MagicMock()
    mock_response.content = '{"executive_summary": "High risk.", "overall_risk_level": "high", "findings": [], "recommendations": ["upgrade lodash"]}'
    with patch("src.main_graph.nodes.report_builder._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        result = await report_builder(_make_state(findings=findings))
    assert result["analysis_report"]["overall_risk_level"] == "high"
    assert "generated_at" in result["analysis_report"]
```

- [ ] **Step 3: Run tests**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_report_builder.py -v
```

Expected: 2 PASSED

- [ ] **Step 4: Commit**

```bash
git add src/main_graph/nodes/report_builder.py tests/unit/nodes/test_report_builder.py
git commit -m "feat: rewrite report_builder — LLM-based formatting of FindingNote list"
```

---

### Task 13: New main graph

**Files:**
- Modify: `src/main_graph/graph.py`

**Interfaces:**
- Consumes: all nodes from Tasks 9-12, `discovery_subgraph`, `MainState`
- Produces: `main_graph` — compiled LangGraph with InMemorySaver

- [ ] **Step 1: Rewrite graph.py**

Replace `src/main_graph/graph.py` with:

```python
"""Main graph — ReAct conductor loop."""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    CONDUCTOR,
    HITL_GATE,
    PREP,
    REPORT_BUILDER,
    TOOL_RUNNER,
)
from src.main_graph.nodes.conductor import conductor
from src.main_graph.nodes.hitl_gate import hitl_gate
from src.main_graph.nodes.report_builder import report_builder
from src.main_graph.nodes.tool_runner import tool_runner
from src.main_graph.state import MainState
from src.main_graph.subgraphs.discovery import discovery_subgraph


def _after_prep(state: MainState) -> str:
    if state.get("discovery_error"):
        return END
    return CONDUCTOR


def _after_conductor(state: MainState) -> str:
    decision = state.get("conductor_decision")
    if decision is None:
        return REPORT_BUILDER
    if decision.finalize:
        return REPORT_BUILDER
    if decision.ask_user or decision.checkpoint_message:
        return HITL_GATE
    if decision.tool_calls:
        return TOOL_RUNNER
    return REPORT_BUILDER


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(PREP, discovery_subgraph)
    builder.add_node(CONDUCTOR, conductor)
    builder.add_node(TOOL_RUNNER, tool_runner)
    builder.add_node(HITL_GATE, hitl_gate)
    builder.add_node(REPORT_BUILDER, report_builder)

    builder.add_edge(START, PREP)
    builder.add_conditional_edges(PREP, _after_prep, [CONDUCTOR, END])
    builder.add_conditional_edges(CONDUCTOR, _after_conductor, [TOOL_RUNNER, HITL_GATE, REPORT_BUILDER])
    builder.add_edge(TOOL_RUNNER, CONDUCTOR)
    builder.add_edge(HITL_GATE, CONDUCTOR)
    builder.add_edge(REPORT_BUILDER, END)

    return builder.compile(checkpointer=InMemorySaver())


main_graph = build_main_graph()
```

- [ ] **Step 2: Verify the graph compiles**

```bash
cd apps/backend && uv run python -c "
from src.main_graph.graph import main_graph
nodes = list(main_graph.graph.nodes.keys())
print('nodes:', nodes)
assert 'prep' in nodes
assert 'conductor' in nodes
assert 'tool_runner' in nodes
assert 'hitl_gate' in nodes
assert 'report_builder' in nodes
print('graph ok')
"
```

Expected: prints `graph ok`

- [ ] **Step 3: Commit**

```bash
git add src/main_graph/graph.py
git commit -m "feat: wire new 5-node ReAct conductor graph (prep → conductor ⟷ tool_runner → hitl_gate → report_builder)"
```

---

### Task 14: Update job_runner and API

**Files:**
- Modify: `src/services/job_runner.py` — full artifact tracking, cache cleanup, autopilot
- Modify: `src/api/schemas.py` — add autopilot field
- Modify: `src/api/routes.py` — pass autopilot

**Interfaces:**
- Produces: full artifact streaming for all 5 nodes; `autopilot` field accepted in POST /analyze

- [ ] **Step 1: Rewrite job_runner.py with proper artifact tracking**

Replace `src/services/job_runner.py` with:

```python
"""Background task: run a job through the ReAct conductor pipeline."""
from __future__ import annotations

import logging
import shutil

from langgraph.types import Command

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph import main_graph
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.main_graph.constants import CONDUCTOR, HITL_GATE, PREP, REPORT_BUILDER, TOOL_RUNNER
from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool
from src.main_graph.tools.external_api import clear_cache
from src.models.job import JobStatus

logger = logging.getLogger(__name__)


def _build_config(job_id: str, dao: JobRepositoryPort) -> dict:
    container = DockerContainerAdapter()
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "container": container,
            "docker_tool": make_docker_tool(container),
        }
    }


async def _stream_graph(graph, input_data, config, dao: JobRepositoryPort, job_id: str) -> bool:
    """Stream graph updates and track artifacts. Returns True if interrupted."""
    interrupted = False

    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            if node_name == "__interrupt__":
                interrupted = True
                continue

            logger.info("job=%s node=%s completed", job_id, node_name)

            if node_name == PREP:
                if node_update.get("discovery_error"):
                    await dao.complete_artifact(job_id, PREP, "failed")
                else:
                    await dao.complete_artifact(job_id, PREP, "done")
                    await dao.start_artifact(job_id, CONDUCTOR)

            elif node_name == CONDUCTOR:
                decision = node_update.get("conductor_decision")
                if decision:
                    await dao.update_artifact_data(job_id, CONDUCTOR, {
                        "iteration": node_update.get("conductor_iteration"),
                        "tool_calls": [tc.model_dump() for tc in decision.tool_calls],
                        "findings_count": len(node_update.get("findings") or []),
                        "finalize": decision.finalize,
                        "reasoning": decision.reasoning,
                    })

            elif node_name == TOOL_RUNNER:
                results = node_update.get("tool_results") or []
                await dao.update_artifact_data(job_id, TOOL_RUNNER, {
                    "tools_run": [tr.tool for tr in results],
                    "errors": [tr.tool for tr in results if tr.error],
                })

            elif node_name == HITL_GATE:
                await dao.start_artifact(job_id, HITL_GATE)

            elif node_name == REPORT_BUILDER:
                await dao.start_artifact(job_id, REPORT_BUILDER)
                if "analysis_report" in node_update:
                    await dao.update_artifact_data(job_id, REPORT_BUILDER, {
                        "output": node_update["analysis_report"]
                    })
                await dao.complete_artifact(job_id, REPORT_BUILDER, "done")

    return interrupted


async def _finalize(dao: JobRepositoryPort, job_id: str, config: dict) -> None:
    clear_cache()
    snapshot = await main_graph.aget_state(config)
    values = snapshot.values
    if repo_path := values.get("repo_path"):
        shutil.rmtree(repo_path, ignore_errors=True)
    if values.get("cancelled"):
        await dao.mark_cancelled(job_id)
    elif values.get("discovery_error"):
        await dao.mark_failed(job_id, error=values["discovery_error"])
    else:
        await dao.save_result(job_id, {"analysis_report": values.get("analysis_report")})


async def run_analysis(
    job_id: str,
    repo_url: str,
    concern: str,
    autopilot: bool,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.running)
    await dao.start_artifact(job_id, PREP)
    config = _build_config(job_id, dao)
    clear_cache()

    try:
        interrupted = await _stream_graph(
            main_graph,
            {
                "repo_url": repo_url,
                "concern": concern,
                "job_id": job_id,
                "autopilot": autopilot,
                "messages": [],
                "tool_results": [],
                "findings": [],
            },
            config,
            dao,
            job_id,
        )
        if interrupted:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return
        await _finalize(dao, job_id, config)
        await dao.update_status(job_id, JobStatus.done)

    except Exception as exc:
        logger.exception("job=%s unhandled error", job_id)
        clear_cache()
        await dao.mark_failed(job_id, error=str(exc))


async def resume_analysis(
    job_id: str,
    user_message: str,
    dao: JobRepositoryPort,
) -> None:
    await dao.update_status(job_id, JobStatus.processing)
    config = _build_config(job_id, dao)

    try:
        interrupted = await _stream_graph(
            main_graph,
            Command(resume=user_message),
            config,
            dao,
            job_id,
        )
        if interrupted:
            await dao.update_status(job_id, JobStatus.awaiting_approval)
            return
        await _finalize(dao, job_id, config)
        await dao.update_status(job_id, JobStatus.done)

    except Exception as exc:
        logger.exception("job=%s unhandled error on resume", job_id)
        clear_cache()
        await dao.mark_failed(job_id, error=str(exc))
```

- [ ] **Step 2: Update schemas.py — add autopilot**

In `src/api/schemas.py`, update `AnalysisRequest`:

```python
class AnalysisRequest(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False
```

- [ ] **Step 3: Update routes.py — pass autopilot**

In `src/api/routes.py`, update the `analyze` route:

```python
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
            autopilot=request.autopilot,
            dao=dao,
        )
    )
    return {"trace_id": job.id, "status": job.status}
```

- [ ] **Step 4: Update test_job_runner.py**

Open `tests/unit/services/test_job_runner.py`. Update any call to `run_analysis` to include `autopilot=False`. Remove references to `vector_store`, `sbom_dao`.

- [ ] **Step 5: Verify backend starts**

```bash
cd apps/backend && uv run python -c "from src.main import app; print('FastAPI app ok')"
```

Expected: `FastAPI app ok`

- [ ] **Step 6: Run all remaining tests**

```bash
cd apps/backend && uv run pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass (some integration tests may skip due to missing env vars — that is acceptable)

- [ ] **Step 7: Commit**

```bash
git add src/services/job_runner.py src/api/schemas.py src/api/routes.py tests/unit/services/test_job_runner.py
git commit -m "feat: update job_runner with new artifact tracking; add autopilot to API"
```

---

### Task 15: Update HITL docs and architecture tests

**Files:**
- Modify: `docs/backend/hitl.md` — update gate names and artifact shapes
- Modify: `tests/architecture/test_boundaries.py` — update for new module structure

**Interfaces:**
- Produces: correct docs for the new HITL contract; architecture tests pass

- [ ] **Step 1: Update hitl.md**

Replace the two gate sections (Gate 1 and Gate 2) in `docs/backend/hitl.md` with:

```markdown
## Gate: hitl_gate

**When:** The conductor emits `ask_user` or `checkpoint_message` and `autopilot=false`.

**Artifact shape:**

\`\`\`typescript
{
  node: "hitl_gate",
  status: "running",
  data: {},
  messages: [
    {
      role: "assistant",
      content: string,          // question or checkpoint summary
      created_at: string,       // ISO 8601
      type: "ask_user" | "checkpoint"
    }
    // after /chat: { role: "human", content: string, created_at: string }
  ]
}
\`\`\`

**Detecting the active gate:**

\`\`\`typescript
function isAwaitingInput(artifacts: Artifact[]): boolean {
  const gate = artifacts.find(a => a.node === "hitl_gate");
  return gate?.status === "running" && (gate.messages?.length ?? 0) > 0;
}
\`\`\`
```

Also update `getActiveGate` helper to reference `"hitl_gate"` instead of `"investigation_planner"` and `"finding_reviewer"`.

- [ ] **Step 2: Fix architecture boundary tests**

Open `tests/architecture/test_boundaries.py`. Remove imports/assertions for deleted modules (`skills`, `evidence`, `risk_finding`, `investigation_plan`). Verify it passes:

```bash
cd apps/backend && uv run pytest tests/architecture/ -v
```

- [ ] **Step 3: Run full test suite one final time**

```bash
cd apps/backend && uv run pytest tests/ -v --tb=short
```

Expected: all passing

- [ ] **Step 4: Final commit**

```bash
git add docs/backend/hitl.md tests/architecture/
git commit -m "docs: update HITL contract for hitl_gate node; fix architecture tests"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All 5 nodes covered (prep=T4, conductor=T9, tool_runner=T10, hitl_gate=T11, report_builder=T12). All 21 tools covered (npm_cli=T6: 3 tools, package_files=T7: 13 tools, external_api=T8: 6 tools — total 22, but `deprecated_packages` and `breaking_updates` from spec are wrapped inside npm_outdated output; they can be added as separate tools in a follow-up). MaxIterations guard in conductor (T9). Autopilot in conductor + hitl_gate (T9, T11). Concern chaining via messages (natural — messages accumulate). State simplification (T3). Prep subgraph update (T4).

- [x] **Placeholder scan:** No TBDs. All code is complete and runnable.

- [x] **Type consistency:** `ConductorDecision` defined in T2, consumed in T9 (returns), T10 (reads), T11 (reads), T13 (routing). `FindingNote` defined in T2, accumulated in T9, read in T12. `ToolResult` defined in T2, produced in T10, read by conductor in T9. All match.

- [x] **Missing tools note:** `deprecated_packages` and `breaking_updates` from the spec's version analysis group are not implemented as standalone tools in this plan; `npm_outdated` (T6) already surfaces the underlying data. Add them as thin wrappers in a follow-up.
