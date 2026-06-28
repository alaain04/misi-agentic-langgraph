# Agentic Discovery Orchestrator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5-node discovery pipeline with a single ReAct agent (`discovery_orchestrator`) that clones, inspects, generates the SBOM, and retries on failure — keeping `build_dependency_summary` as a downstream node.

**Architecture:** A `create_agent`-based ReAct agent receives `run_docker_command`, `list_dir`, and `read_file` as tools, and returns a structured `OrchestratorResult`. The node wrapper persists the SBOM to MongoDB after the agent returns. The graph shrinks to two nodes: `discovery_orchestrator → build_dependency_summary`.

**Tech Stack:** Python, LangGraph, LangChain (`create_agent`, `@tool`), Pydantic, pytest, pytest-asyncio.

## Global Constraints

- Use `uv run pytest` — never `python -m pytest`.
- All async tests must be marked `@pytest.mark.asyncio`.
- No new dependencies — use existing `langchain`, `langgraph`, `pydantic`.
- Node model: `Model.GPT_5_4` (same as `inspector_agent`).
- `run_docker_command` is not a module-level tool — it comes from `make_docker_tool(container)` at call time via `get_services(config)["docker_tool"]`.
- `list_dir` and `read_file` are module-level `@tool` functions from `tools/filesystem.py`; import them directly.
- The output contract to `MainState` is unchanged — same field names and types as today.

---

## File Map

| Action | Path |
|--------|------|
| Create | `src/main_graph/subgraphs/discovery/nodes/discovery_orchestrator.py` |
| Create | `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py` |
| Modify | `src/main_graph/subgraphs/discovery/constants.py` |
| Modify | `src/main_graph/subgraphs/discovery/nodes/__init__.py` |
| Modify | `src/main_graph/subgraphs/discovery/graph.py` |
| Modify | `src/main_graph/subgraphs/discovery/state.py` |
| Modify | `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py` |
| Delete | `src/main_graph/subgraphs/discovery/nodes/clone_repository.py` |
| Delete | `src/main_graph/subgraphs/discovery/nodes/inspector_agent.py` |
| Delete | `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py` |
| Delete | `src/main_graph/subgraphs/discovery/nodes/generate_sbom.py` |
| Delete | `src/main_graph/subgraphs/discovery/service.py` |
| Delete | `tests/unit/subgraphs/discovery/test_clone_repository.py` |
| Delete | `tests/unit/subgraphs/discovery/test_inspector_agent.py` |
| Delete | `tests/unit/subgraphs/discovery/test_lock_generator_agent.py` |
| Delete | `tests/unit/subgraphs/discovery/test_generate_sbom.py` |

---

### Task 1: `discovery_orchestrator` node

**Files:**
- Create: `src/main_graph/subgraphs/discovery/nodes/discovery_orchestrator.py`
- Create: `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py`

**Interfaces:**
- Consumes: `DiscoveryState` (`job_id`, `repo_url`, `concern`), `config["configurable"]["docker_tool"]`, `config["configurable"]["sbom_dao"]`
- Produces: `repo_path`, `manifest_files`, `detected_package_manager`, `package_manager_version`, `docker_image`, `sbom_cyclonedx`, `sbom_result_id`, and optionally `sbom_error`, `discovery_error`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator import (
    OrchestratorResult,
    discovery_orchestrator,
)

_SAMPLE_SBOM = {"bomFormat": "CycloneDX", "components": []}

_BASE_STATE = {
    "job_id": "test-job",
    "repo_url": "https://github.com/test/repo",
    "concern": "security",
}

_SUCCESS_RESULT = OrchestratorResult(
    repo_path="/tmp/debug_job_test-job",
    detected_package_manager="npm",
    package_manager_version="latest",
    manifest_files=["package.json", "package-lock.json"],
    docker_image="node:22-alpine",
    sbom_cyclonedx=_SAMPLE_SBOM,
    sbom_error=None,
    discovery_error=None,
)


def _config(sbom_dao):
    return {"configurable": {"sbom_dao": sbom_dao, "docker_tool": MagicMock()}}


@pytest.mark.asyncio
async def test_success_writes_all_state_fields():
    dao = AsyncMock()
    dao.save.return_value = "result-id-1"

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"structured_response": _SUCCESS_RESULT}
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM
    assert result["sbom_result_id"] == "result-id-1"
    assert result["detected_package_manager"] == "npm"
    assert result["docker_image"] == "node:22-alpine"
    assert result["manifest_files"] == ["package.json", "package-lock.json"]
    assert "discovery_error" not in result
    assert "sbom_error" not in result
    dao.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_sbom_error_included_in_state():
    dao = AsyncMock()
    dao.save.return_value = "err-id"
    error_result = OrchestratorResult(
        repo_path="/tmp/debug_job_test-job",
        detected_package_manager="npm",
        package_manager_version="latest",
        manifest_files=["package.json"],
        docker_image="node:22-alpine",
        sbom_cyclonedx={},
        sbom_error="ERESOLVE: could not resolve dependency tree",
        discovery_error=None,
    )

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"structured_response": error_result}
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert result["sbom_cyclonedx"] == {}
    assert result["sbom_error"] == "ERESOLVE: could not resolve dependency tree"
    assert result["sbom_result_id"] == "err-id"
    assert "discovery_error" not in result


@pytest.mark.asyncio
async def test_discovery_error_included_in_state():
    dao = AsyncMock()
    dao.save.return_value = "clone-fail-id"
    clone_fail_result = OrchestratorResult(
        repo_path="/tmp/debug_job_test-job",
        detected_package_manager="npm",
        package_manager_version="latest",
        manifest_files=[],
        docker_image="node:lts-alpine",
        sbom_cyclonedx={},
        sbom_error=None,
        discovery_error="git clone failed: repository not found",
    )

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"structured_response": clone_fail_result}
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert result["discovery_error"] == "git clone failed: repository not found"
    assert result["sbom_cyclonedx"] == {}


@pytest.mark.asyncio
async def test_agent_exception_returns_discovery_error():
    dao = AsyncMock()
    dao.save.return_value = "crash-id"

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = RuntimeError("agent crashed")
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert "discovery_error" in result
    assert "agent crashed" in result["discovery_error"]
    assert result["sbom_cyclonedx"] == {}
    assert result["sbom_result_id"] == "crash-id"
    dao.save.assert_awaited_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_discovery_orchestrator.py -v
```
Expected: `ImportError` — `discovery_orchestrator` module does not exist yet.

- [ ] **Step 3: Implement the node**

Create `src/main_graph/subgraphs/discovery/nodes/discovery_orchestrator.py`:

```python
"""Node: discovery_orchestrator — single ReAct agent for all discovery work."""

import logging
import os

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.main_graph.subgraphs.discovery.tools.filesystem import list_dir, read_file
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4)

_SYSTEM = """\
You are a Node.js dependency discovery agent. Your goal is to clone a repository, \
inspect it, generate a valid CycloneDX SBOM, and return a structured result.

You have these tools:
- run_docker_command(image, volume, command): run a shell command in a Docker container
- list_dir(path): list files at a local path on the host
- read_file(path): read a local file on the host

## Step 1 — Clone

Run:
  run_docker_command(
    image="alpine/git",
    volume="{tmp_dir}:/workspace",
    command="git clone --depth=1 --single-branch {repo_url} /workspace"
  )

If returncode != 0, set discovery_error to the stderr and stop — return the result now.
repo_path is always "{tmp_dir}".

## Step 2 — Inspect

Use list_dir and read_file on "{tmp_dir}" to determine:
- detected_package_manager: check which lock file exists:
    pnpm-lock.yaml → "pnpm", yarn.lock → "yarn", package-lock.json → "npm"; default: "npm"
- package_manager_version: read package.json "packageManager" field (e.g. "pnpm@9.15.0" → "9.15.0");
    strip any hash suffix; default: "latest"
- manifest_files: list of ["package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"]
    that actually exist
- docker_image: select to satisfy BOTH engines.node AND package manager Node requirement:
    pnpm v11+ requires Node >=22; otherwise follow engines.node; take the higher.
    Examples: engines.node=">=20" + pnpm@11 → "node:22-alpine"
              engines.node=">=22" + npm → "node:22-alpine"
              engines.node=">=20" + npm → "node:20-alpine"
    Fallback if unreadable: "node:lts-alpine"

## Step 3 — Generate SBOM

volume for all SBOM commands: "{tmp_dir}:/workspace"

### If a lock file IS present — try SBOM directly (no install needed):

  pnpm-lock.yaml:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install -g pnpm@{{pm_version}} && pnpm sbom --sbom-format=cyclonedx")
    (replace {{pm_version}} with the version you detected in Step 2, e.g. "9.15.0" or "latest")

  package-lock.json:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm sbom --sbom-format=cyclonedx --package-lock-only")

  yarn.lock:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install --package-lock-only --ignore-scripts && NO_UPDATE_NOTIFIER=1 npm sbom --sbom-format=cyclonedx --package-lock-only")

### If NO lock file — generate one first, then SBOM:

  pnpm:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install -g pnpm@{pm_version} && pnpm install")
  npm/yarn:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install --ignore-scripts")

  Verify the lock file was created with read_file. Then run the SBOM command for the detected pm.

## Step 4 — Retry strategy (max 8 total SBOM attempts)

When a SBOM or install command fails, read the stderr and adapt:
- "ERESOLVE" or "peer" conflict → append --legacy-peer-deps to the npm command; if that also fails, use --force
- "pnpm: command not found" or pnpm exits non-zero → fall back to the npm command for the same lock
- Node version error ("requires Node") → switch docker_image to "node:22-alpine" then "node:20-alpine"
- Any other failure → try --legacy-peer-deps first, then --force as last resort

Each retry is a new run_docker_command call. Count attempts. After 8 failures stop and set sbom_error.

## Step 5 — SBOM output

On success, stdout contains the raw CycloneDX JSON. Parse it as sbom_cyclonedx.
On total failure, set sbom_cyclonedx={{}} and sbom_error to the last error message.
"""


class OrchestratorResult(BaseModel):
    repo_path: str
    detected_package_manager: str
    package_manager_version: str
    manifest_files: list[str]
    docker_image: str
    sbom_cyclonedx: dict
    sbom_error: str | None = None
    discovery_error: str | None = None


async def discovery_orchestrator(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    docker_tool = svc["docker_tool"]
    sbom_dao = svc["sbom_dao"]

    job_id = state["job_id"]
    repo_url = state["repo_url"]
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    agent = create_agent(
        model=_llm,
        tools=[docker_tool, list_dir, read_file],
        response_format=OrchestratorResult,
    )

    try:
        agent_result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM.format(tmp_dir=tmp_dir, repo_url=repo_url)),
                    HumanMessage(content="Run the full discovery now."),
                ]
            },
            config={"recursion_limit": 40},
        )
        output: OrchestratorResult = agent_result["structured_response"]
    except Exception as exc:
        logger.exception("discovery_orchestrator: agent failed")
        entry = SbomEntry(repo_url=repo_url, scan_error=str(exc))
        result_id = await sbom_dao.save(entry)
        return {
            "repo_path": tmp_dir,
            "manifest_files": [],
            "detected_package_manager": "npm",
            "package_manager_version": "latest",
            "docker_image": "node:lts-alpine",
            "sbom_cyclonedx": {},
            "sbom_result_id": result_id,
            "discovery_error": f"Discovery agent failed: {exc}",
        }

    entry = SbomEntry(
        repo_url=repo_url,
        sbom_cyclonedx=output.sbom_cyclonedx,
        scan_error=output.sbom_error,
    )
    result_id = await sbom_dao.save(entry)
    logger.info(
        "discovery_orchestrator: done pm=%s sbom_error=%s discovery_error=%s",
        output.detected_package_manager,
        output.sbom_error,
        output.discovery_error,
    )

    out: dict = {
        "repo_path": output.repo_path,
        "manifest_files": output.manifest_files,
        "detected_package_manager": output.detected_package_manager,
        "package_manager_version": output.package_manager_version,
        "docker_image": output.docker_image,
        "sbom_cyclonedx": output.sbom_cyclonedx,
        "sbom_result_id": result_id,
    }
    if output.sbom_error:
        out["sbom_error"] = output.sbom_error
    if output.discovery_error:
        out["discovery_error"] = output.discovery_error
    return out
```

> **Note on `_SYSTEM` format string:** the literal `{pm_version}` inside the SBOM commands is intentional — the agent reads the actual `package_manager_version` it detected in Step 2 and substitutes it itself. Pass it through by escaping the braces: the `.format(tmp_dir=..., repo_url=..., pm_version="{pm_version}")` call keeps `{pm_version}` verbatim in the system prompt so the agent knows to fill it in.

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_discovery_orchestrator.py -v
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/discovery_orchestrator.py \
        tests/unit/subgraphs/discovery/test_discovery_orchestrator.py
git commit -m "feat: add discovery_orchestrator ReAct agent node"
```

---

### Task 2: Wire up graph (constants, nodes `__init__`, graph)

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/constants.py`
- Modify: `src/main_graph/subgraphs/discovery/nodes/__init__.py`
- Modify: `src/main_graph/subgraphs/discovery/graph.py`

**Interfaces:**
- Consumes: `discovery_orchestrator` from Task 1
- Produces: 2-node compiled subgraph (`discovery_orchestrator → build_dependency_summary`)

- [ ] **Step 1: Update constants**

Replace the entire contents of `src/main_graph/subgraphs/discovery/constants.py`:

```python
"""Node name constants for the discovery subgraph."""

DISCOVERY_ORCHESTRATOR = "discovery_orchestrator"
BUILD_DEPENDENCY_SUMMARY = "build_dependency_summary"
```

- [ ] **Step 2: Update nodes `__init__`**

Replace the entire contents of `src/main_graph/subgraphs/discovery/nodes/__init__.py`:

```python
from src.main_graph.subgraphs.discovery.nodes.build_dependency_summary import (
    build_dependency_summary,
)
from src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator import (
    discovery_orchestrator,
)

__all__ = [
    "discovery_orchestrator",
    "build_dependency_summary",
]
```

- [ ] **Step 3: Update graph**

Replace the entire contents of `src/main_graph/subgraphs/discovery/graph.py`:

```python
"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    DISCOVERY_ORCHESTRATOR,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    discovery_orchestrator,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(DISCOVERY_ORCHESTRATOR, discovery_orchestrator)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, DISCOVERY_ORCHESTRATOR)
    builder.add_edge(DISCOVERY_ORCHESTRATOR, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
```

- [ ] **Step 4: Run import smoke test**

```bash
cd apps/backend && uv run python -c "from src.main_graph.subgraphs.discovery.graph import discovery_subgraph; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/discovery/constants.py \
        src/main_graph/subgraphs/discovery/nodes/__init__.py \
        src/main_graph/subgraphs/discovery/graph.py
git commit -m "feat: wire discovery subgraph to single orchestrator node"
```

---

### Task 3: Slim `DiscoveryState` + fix `build_dependency_summary`

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/state.py`
- Modify: `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`

**Interfaces:**
- Consumes: nothing new — internal cleanup only
- Produces: `DiscoveryState` without lock-gen intermediate fields; `build_dependency_summary` no longer references `lock_generation_error`

- [ ] **Step 1: Remove intermediate fields from `DiscoveryState`**

Replace the entire contents of `src/main_graph/subgraphs/discovery/state.py`:

```python
"""State schema for the discovery subgraph."""

from typing import Any, NotRequired

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

    # set by discovery_orchestrator
    repo_path: NotRequired[str]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    package_manager_version: NotRequired[str]
    docker_image: NotRequired[str]
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # outputs
    project_metadata: NotRequired[ProjectMetadata]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
```

- [ ] **Step 2: Remove `lock_generation_error` reference from `build_dependency_summary`**

In `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`, find and replace this exact block:

```python
    lock_note = ""
    if state.get("lock_generation_error"):
        err = state["lock_generation_error"]
        lock_note = (
            f"\nNote: lock file generation failed ({err}); SBOM may be incomplete."
        )
```

Replace with:

```python
    lock_note = ""
    if state.get("sbom_error"):
        lock_note = (
            f"\nNote: SBOM generation encountered an error ({state['sbom_error']}); "
            "dependency list may be incomplete."
        )
```

- [ ] **Step 3: Run affected tests**

```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/test_build_dependency_summary.py -v
```
Expected: all PASSED (no test references `lock_generation_error`).

- [ ] **Step 4: Commit**

```bash
git add src/main_graph/subgraphs/discovery/state.py \
        src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py
git commit -m "refactor: remove lock-gen intermediate fields from DiscoveryState"
```

---

### Task 4: Delete dead code and stale tests

**Files:**
- Delete: `src/main_graph/subgraphs/discovery/nodes/clone_repository.py`
- Delete: `src/main_graph/subgraphs/discovery/nodes/inspector_agent.py`
- Delete: `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py`
- Delete: `src/main_graph/subgraphs/discovery/nodes/generate_sbom.py`
- Delete: `src/main_graph/subgraphs/discovery/service.py`
- Delete: `tests/unit/subgraphs/discovery/test_clone_repository.py`
- Delete: `tests/unit/subgraphs/discovery/test_inspector_agent.py`
- Delete: `tests/unit/subgraphs/discovery/test_lock_generator_agent.py`
- Delete: `tests/unit/subgraphs/discovery/test_generate_sbom.py`

- [ ] **Step 1: Delete dead node files and service**

```bash
rm src/main_graph/subgraphs/discovery/nodes/clone_repository.py
rm src/main_graph/subgraphs/discovery/nodes/inspector_agent.py
rm src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py
rm src/main_graph/subgraphs/discovery/nodes/generate_sbom.py
rm src/main_graph/subgraphs/discovery/service.py
```

- [ ] **Step 2: Delete stale test files**

```bash
rm tests/unit/subgraphs/discovery/test_clone_repository.py
rm tests/unit/subgraphs/discovery/test_inspector_agent.py
rm tests/unit/subgraphs/discovery/test_lock_generator_agent.py
rm tests/unit/subgraphs/discovery/test_generate_sbom.py
```

- [ ] **Step 3: Run the full discovery test suite**

```bash
cd apps/backend && uv run pytest tests/unit/subgraphs/discovery/ -v
```
Expected: only `test_build_dependency_summary.py` and `test_discovery_orchestrator.py` run; all PASSED.

- [ ] **Step 4: Run the full test suite to catch any lingering imports**

```bash
cd apps/backend && uv run pytest tests/unit/ -v
```
Expected: all PASSED; no `ImportError` from deleted modules.

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "refactor: delete discovery pipeline nodes replaced by orchestrator"
```
