# Agentic Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static discovery pipeline with two `create_react_agent` agents — an inspector that reads manifests and a lock generator that installs dependencies and repairs errors — feeding into the existing Trivy SBOM scan and LLM summary.

**Architecture:** `clone_repository` (deterministic git clone) → `inspector_agent` (ReAct: reads manifests, detects PM + lock file presence) → optional `lock_generator_agent` (ReAct: runs install in Docker, reads errors, fixes package.json, retries ≤6 cycles) → `generate_sbom` (Trivy CycloneDX) → `build_dependency_summary` (LLM summary). Error short-circuits at clone and inspector route directly to `build_dependency_summary`.

**Tech Stack:** Python 3.12, LangGraph 1.x (`create_react_agent`, `response_format`), LangChain `@tool`, Pydantic, Docker (alpine/git + node images), Trivy.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `src/main_graph/subgraphs/discovery/state.py` | Modify | Add inspector + lock generator state fields |
| `src/main_graph/subgraphs/discovery/constants.py` | Modify | Add 3 new node name constants |
| `src/main_graph/subgraphs/discovery/nodes/fetch_repository.py` | Delete | Replaced by clone_repository.py |
| `src/main_graph/subgraphs/discovery/nodes/clone_repository.py` | Create | Git clone only, no LLM |
| `src/main_graph/subgraphs/discovery/nodes/inspector_agent.py` | Create | ReAct agent: list_dir + read_file tools |
| `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py` | Create | ReAct agent: run_docker_command + read_file + write_file tools |
| `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py` | Modify | Add lock_generation_error context to prompt |
| `src/main_graph/subgraphs/discovery/nodes/__init__.py` | Modify | Update imports |
| `src/main_graph/subgraphs/discovery/graph.py` | Modify | 5-node topology + new routing |
| `tests/unit/subgraphs/discovery/test_fetch_repository.py` | Delete | Superseded |
| `tests/unit/subgraphs/discovery/test_clone_repository.py` | Create | Clone-only tests |
| `tests/unit/subgraphs/discovery/test_inspector_agent.py` | Create | Tool unit tests + node mock test |
| `tests/unit/subgraphs/discovery/test_lock_generator_agent.py` | Create | Tool unit tests + node mock test |

All paths are relative to `backend/`.

---

## Task 1: Update state schema and constants

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/state.py`
- Modify: `src/main_graph/subgraphs/discovery/constants.py`

- [ ] **Step 1: Replace state.py**

Replace the full file content with:

```python
"""State schema for the ProjectDiscovery subgraph."""

from typing import Any, NotRequired
from typing_extensions import TypedDict


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int


class DiscoveryState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str

    # set by clone_repository
    repo_path: NotRequired[str]

    # set by inspector_agent
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]   # "npm" | "yarn" | "pnpm"
    lock_file_missing: NotRequired[bool]
    docker_image: NotRequired[str]               # e.g. "node:22-alpine"
    install_command: NotRequired[str]            # e.g. "npm install"

    # set by lock_generator_agent
    lock_generation_attempts: NotRequired[int]
    lock_generation_error: NotRequired[str | None]

    # set by generate_sbom
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # outputs
    project_metadata: NotRequired[ProjectMetadata]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
```

- [ ] **Step 2: Replace constants.py**

```python
"""Node name constants for the ProjectDiscovery subgraph."""

CLONE_REPOSITORY = "clone_repository"
INSPECTOR_AGENT = "inspector_agent"
LOCK_GENERATOR_AGENT = "lock_generator_agent"
GENERATE_SBOM = "generate_sbom"
BUILD_DEPENDENCY_SUMMARY = "build_dependency_summary"
```

- [ ] **Step 3: Commit**

```bash
git add src/main_graph/subgraphs/discovery/state.py \
        src/main_graph/subgraphs/discovery/constants.py
git commit -m "feat(discovery): expand state schema and constants for agentic pipeline"
```

---

## Task 2: Refactor fetch_repository → clone_repository

**Files:**
- Create: `src/main_graph/subgraphs/discovery/nodes/clone_repository.py`
- Delete: `src/main_graph/subgraphs/discovery/nodes/fetch_repository.py`
- Create: `tests/unit/subgraphs/discovery/test_clone_repository.py`
- Delete: `tests/unit/subgraphs/discovery/test_fetch_repository.py`

- [ ] **Step 1: Write failing tests for clone_repository**

Create `tests/unit/subgraphs/discovery/test_clone_repository.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.clone_repository import clone_repository


@pytest.mark.asyncio
async def test_clone_success_returns_repo_path():
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b""))):
        result = await clone_repository(
            {"repo_url": "https://github.com/test/repo", "concern": "security", "job_id": "99"}
        )

    assert "repo_path" in result
    assert "discovery_error" not in result


@pytest.mark.asyncio
async def test_clone_empty_url_returns_error():
    result = await clone_repository({"repo_url": "", "concern": "security", "job_id": "99"})
    assert result == {"discovery_error": "No repository URL provided"}


@pytest.mark.asyncio
async def test_clone_failure_returns_error():
    mock_proc = MagicMock()
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b"repository not found"))):
        result = await clone_repository(
            {"repo_url": "https://github.com/bad/repo", "concern": "security", "job_id": "99"}
        )

    assert "discovery_error" in result
    assert "git clone failed" in result["discovery_error"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run pytest tests/unit/subgraphs/discovery/test_clone_repository.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — `clone_repository` does not exist yet.

- [ ] **Step 3: Create clone_repository.py**

Create `src/main_graph/subgraphs/discovery/nodes/clone_repository.py`:

```python
"""Node: clone_repository — git clone via Docker."""

import asyncio
import logging
import os

from src.main_graph.subgraphs.discovery.state import DiscoveryState

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 120


async def _docker_run(args: list[str], timeout: int) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return -1, f"timed out after {timeout}s"
    return proc.returncode, stderr_bytes.decode(errors="replace").strip()


def _create_tmp_dir(job_id: str) -> str:
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    return tmp_dir


async def clone_repository(state: DiscoveryState) -> dict:
    repo_url = state.get("repo_url", "").strip()
    if not repo_url:
        return {"discovery_error": "No repository URL provided"}

    tmp_dir = _create_tmp_dir(state["job_id"])
    volume = f"{tmp_dir}:/workspace"
    user = f"{os.getuid()}:{os.getgid()}"

    logger.info("clone_repository: cloning %s into %s", repo_url, tmp_dir)

    returncode, stderr = await _docker_run(
        [
            "docker", "run", "--rm",
            "--user", user,
            "-v", volume,
            "alpine/git", "clone", "--depth=1", "--single-branch", repo_url, "/workspace",
        ],
        timeout=_CLONE_TIMEOUT,
    )
    if returncode != 0:
        logger.error("clone_repository: clone failed: %s", stderr[:300])
        return {"discovery_error": f"git clone failed: {stderr[:300]}"}

    logger.info("clone_repository: cloned %s", repo_url)
    return {"repo_path": tmp_dir}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/subgraphs/discovery/test_clone_repository.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Delete old files**

```bash
rm src/main_graph/subgraphs/discovery/nodes/fetch_repository.py
rm tests/unit/subgraphs/discovery/test_fetch_repository.py
```

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/clone_repository.py \
        tests/unit/subgraphs/discovery/test_clone_repository.py
git rm src/main_graph/subgraphs/discovery/nodes/fetch_repository.py \
       tests/unit/subgraphs/discovery/test_fetch_repository.py
git commit -m "feat(discovery): replace fetch_repository with clone_repository (git clone only)"
```

---

## Task 3: Implement inspector_agent

**Files:**
- Create: `src/main_graph/subgraphs/discovery/nodes/inspector_agent.py`
- Create: `tests/unit/subgraphs/discovery/test_inspector_agent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/subgraphs/discovery/test_inspector_agent.py`:

```python
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.inspector_agent import (
    list_dir,
    read_file,
    inspector_agent,
)


# --- Tool unit tests (real filesystem) ---

def test_list_dir_returns_entries():
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "package.json"), "w").close()
        open(os.path.join(tmp, "yarn.lock"), "w").close()
        result = list_dir.invoke({"path": tmp})
        assert "package.json" in result
        assert "yarn.lock" in result


def test_list_dir_missing_path():
    result = list_dir.invoke({"path": "/nonexistent/path/xyz"})
    assert "not found" in result.lower() or "error" in result.lower()


def test_read_file_returns_content():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write('{"name": "my-app"}')
        path = f.name
    try:
        result = read_file.invoke({"path": path})
        assert "my-app" in result
    finally:
        os.unlink(path)


def test_read_file_missing_file():
    result = read_file.invoke({"path": "/nonexistent/file.json"})
    assert "not found" in result.lower() or "error" in result.lower()


# --- Node integration test (mocked agent) ---

@pytest.mark.asyncio
async def test_inspector_agent_maps_structured_response_to_state():
    from src.main_graph.subgraphs.discovery.nodes.inspector_agent import InspectorResult

    mock_output = InspectorResult(
        detected_package_manager="yarn",
        lock_file_missing=False,
        manifest_files=["package.json", "yarn.lock"],
        docker_image="node:lts-alpine",
        install_command="yarn install",
    )

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.inspector_agent._agent"
    ) as mock_agent:
        mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_output})
        result = await inspector_agent(
            {"repo_url": "https://github.com/x/y", "concern": "security", "job_id": "1", "repo_path": "/tmp/repo"}
        )

    assert result["detected_package_manager"] == "yarn"
    assert result["lock_file_missing"] is False
    assert result["manifest_files"] == ["package.json", "yarn.lock"]
    assert result["docker_image"] == "node:lts-alpine"
    assert result["install_command"] == "yarn install"


@pytest.mark.asyncio
async def test_inspector_agent_returns_error_when_no_repo_path():
    result = await inspector_agent(
        {"repo_url": "https://github.com/x/y", "concern": "security", "job_id": "1"}
    )
    assert "discovery_error" in result


@pytest.mark.asyncio
async def test_inspector_agent_returns_error_on_agent_exception():
    with patch(
        "src.main_graph.subgraphs.discovery.nodes.inspector_agent._agent"
    ) as mock_agent:
        mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        result = await inspector_agent(
            {"repo_url": "https://github.com/x/y", "concern": "security", "job_id": "1", "repo_path": "/tmp/repo"}
        )

    assert "discovery_error" in result
    assert "Inspector agent failed" in result["discovery_error"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/subgraphs/discovery/test_inspector_agent.py -v
```

Expected: `ModuleNotFoundError` — `inspector_agent.py` does not exist yet.

- [ ] **Step 3: Create inspector_agent.py**

Create `src/main_graph/subgraphs/discovery/nodes/inspector_agent.py`:

```python
"""Node: inspector_agent — ReAct agent that reads Node.js manifests."""

import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)


@tool
def list_dir(path: str) -> str:
    """List files and directories at the given absolute path."""
    try:
        return "\n".join(os.listdir(path))
    except FileNotFoundError:
        return f"Directory not found: {path}"
    except Exception as exc:
        return f"Error listing directory: {exc}"


@tool
def read_file(path: str) -> str:
    """Read the contents of a file at the given absolute path."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as exc:
        return f"Error reading file: {exc}"


class InspectorResult(BaseModel):
    detected_package_manager: str  # "npm" | "yarn" | "pnpm"
    lock_file_missing: bool
    manifest_files: list[str]
    docker_image: str   # e.g. "node:22-alpine"
    install_command: str  # e.g. "npm install"


_SYSTEM_TEMPLATE = """\
You are inspecting a cloned Node.js repository at {repo_path}.

Steps:
1. Call list_dir("{repo_path}") to see root contents.
2. Call read_file("{repo_path}/package.json") to read the manifest.
3. Determine the package manager by lock file presence (pnpm-lock.yaml → pnpm, yarn.lock → yarn, package-lock.json → npm; default npm).
4. Set lock_file_missing=true if none of those three files exist in the root.
5. Read engines.node from package.json to pick the Docker image (e.g. "22" → "node:22-alpine"); default "node:lts-alpine".
6. Set install_command to the appropriate command (npm install / yarn install / pnpm install).
7. Return structured output.

If package.json is missing, return: detected_package_manager="npm", lock_file_missing=true, manifest_files=[], docker_image="node:lts-alpine", install_command="npm install".
"""

_agent = create_react_agent(
    model=_llm,
    tools=[list_dir, read_file],
    response_format=InspectorResult,
)


async def inspector_agent(state: DiscoveryState) -> dict:
    repo_path = state.get("repo_path", "")
    if not repo_path:
        return {"discovery_error": "No repo_path available for inspection"}

    try:
        result = await _agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM_TEMPLATE.format(repo_path=repo_path)),
                    HumanMessage(content="Inspect the repository now."),
                ]
            },
            config={"recursion_limit": 15},
        )
        output: InspectorResult = result["structured_response"]
    except Exception as exc:
        logger.exception("inspector_agent: failed")
        return {"discovery_error": f"Inspector agent failed: {exc}"}

    return {
        "detected_package_manager": output.detected_package_manager,
        "lock_file_missing": output.lock_file_missing,
        "manifest_files": output.manifest_files,
        "docker_image": output.docker_image,
        "install_command": output.install_command,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/subgraphs/discovery/test_inspector_agent.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/inspector_agent.py \
        tests/unit/subgraphs/discovery/test_inspector_agent.py
git commit -m "feat(discovery): add inspector_agent with list_dir + read_file tools"
```

---

## Task 4: Implement lock_generator_agent

**Files:**
- Create: `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py`
- Create: `tests/unit/subgraphs/discovery/test_lock_generator_agent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/subgraphs/discovery/test_lock_generator_agent.py`:

```python
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import (
    run_docker_command,
    _make_write_file_tool,
    lock_generator_agent,
)


# --- Tool unit tests ---

@pytest.mark.asyncio
async def test_run_docker_command_returns_json_on_success():
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"ok", b""))):
        result = await run_docker_command.ainvoke({
            "image": "node:lts-alpine",
            "command": "npm install",
            "workspace": "/tmp/repo",
        })

    data = json.loads(result)
    assert data["returncode"] == 0
    assert "ok" in data["stdout"]


@pytest.mark.asyncio
async def test_run_docker_command_returns_json_on_failure():
    mock_proc = MagicMock()
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b"peer conflict"))):
        result = await run_docker_command.ainvoke({
            "image": "node:lts-alpine",
            "command": "npm install",
            "workspace": "/tmp/repo",
        })

    data = json.loads(result)
    assert data["returncode"] == 1
    assert "peer conflict" in data["stderr"]


def test_write_file_tool_writes_within_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        write_file = _make_write_file_tool(tmp)
        write_file.invoke({"relative_path": "package.json", "content": '{"name":"x"}'})
        assert (Path(tmp) / "package.json").read_text() == '{"name":"x"}'


def test_write_file_tool_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        write_file = _make_write_file_tool(tmp)
        result = write_file.invoke({"relative_path": "../../etc/passwd", "content": "bad"})
        assert "outside" in result.lower() or "error" in result.lower()


# --- Node integration test (mocked agent) ---

@pytest.mark.asyncio
async def test_lock_generator_agent_success():
    from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import LockGenResult

    mock_output = LockGenResult(success=True, attempts=2, error=None)

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.lock_generator_agent.create_react_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_output})
        mock_factory.return_value = mock_agent

        result = await lock_generator_agent({
            "repo_url": "https://github.com/x/y",
            "concern": "security",
            "job_id": "1",
            "repo_path": "/tmp/repo",
            "detected_package_manager": "npm",
            "docker_image": "node:lts-alpine",
            "install_command": "npm install",
        })

    assert result["lock_generation_attempts"] == 2
    assert result.get("lock_generation_error") is None


@pytest.mark.asyncio
async def test_lock_generator_agent_records_error_on_exhaustion():
    from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import LockGenResult

    mock_output = LockGenResult(success=False, attempts=6, error="peer conflict unresolved")

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.lock_generator_agent.create_react_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_output})
        mock_factory.return_value = mock_agent

        result = await lock_generator_agent({
            "repo_url": "https://github.com/x/y",
            "concern": "security",
            "job_id": "1",
            "repo_path": "/tmp/repo",
            "detected_package_manager": "npm",
            "docker_image": "node:lts-alpine",
            "install_command": "npm install",
        })

    assert result["lock_generation_attempts"] == 6
    assert result["lock_generation_error"] == "peer conflict unresolved"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/subgraphs/discovery/test_lock_generator_agent.py -v
```

Expected: `ModuleNotFoundError` — `lock_generator_agent.py` does not exist yet.

- [ ] **Step 3: Create lock_generator_agent.py**

Create `src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py`:

```python
"""Node: lock_generator_agent — ReAct agent that installs deps and generates a lock file."""

import asyncio
import json
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from src.main_graph.subgraphs.discovery.nodes.inspector_agent import read_file
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_DOCKER_TIMEOUT = 300


@tool
async def run_docker_command(image: str, command: str, workspace: str) -> str:
    """Run a shell command in a Docker container with the workspace mounted.

    Returns JSON with keys: returncode (int), stdout (str), stderr (str).
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm",
        "-v", f"{workspace}:/workspace",
        image, "sh", "-c", f"cd /workspace && {command}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=_DOCKER_TIMEOUT)
    except TimeoutError:
        proc.kill()
        return json.dumps({"returncode": -1, "stdout": "", "stderr": f"timed out after {_DOCKER_TIMEOUT}s"})
    return json.dumps({
        "returncode": proc.returncode,
        "stdout": stdout_b.decode(errors="replace")[:2000],
        "stderr": stderr_b.decode(errors="replace")[:2000],
    })


def _make_write_file_tool(repo_path: str):
    workspace = Path(repo_path).resolve()

    @tool
    def write_file(relative_path: str, content: str) -> str:
        """Write content to a file within the workspace. Path must be relative to workspace root."""
        target = (workspace / relative_path).resolve()
        if not str(target).startswith(str(workspace)):
            return f"Error: path '{relative_path}' is outside the workspace"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {target}"

    return write_file


class LockGenResult(BaseModel):
    success: bool
    attempts: int
    error: str | None


_SYSTEM_TEMPLATE = """\
You are generating a lock file for a Node.js project.

Workspace: {repo_path}
Package manager: {pm}
Docker image: {image}
Initial install command: {command}

Expected lock file:
- npm  → package-lock.json
- yarn → yarn.lock
- pnpm → pnpm-lock.yaml

Steps:
1. Run run_docker_command(image="{image}", command="{command}", workspace="{repo_path}").
2. If it fails (returncode != 0), read the stderr carefully.
3. Apply one fix per retry:
   - npm peer conflict        → append --legacy-peer-deps to the command
   - npm engine mismatch      → switch to a different node image tag (e.g. node:18-alpine)
   - optional dep failure     → append --ignore-optional
   - version range conflict   → read_file("{repo_path}/package.json"), patch the conflicting range with write_file, then retry
4. Repeat up to 6 total attempts.
5. After each run, verify the lock file exists by calling read_file with its expected path.
6. Report success=true if the lock file is readable, otherwise success=false with the last error.
"""


async def lock_generator_agent(state: DiscoveryState) -> dict:
    repo_path = state.get("repo_path", "")
    pm = state.get("detected_package_manager", "npm")
    image = state.get("docker_image", "node:lts-alpine")
    command = state.get("install_command", "npm install")

    write_file_tool = _make_write_file_tool(repo_path)
    agent = create_react_agent(
        model=_llm,
        tools=[run_docker_command, read_file, write_file_tool],
        response_format=LockGenResult,
    )

    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM_TEMPLATE.format(
                        repo_path=repo_path, pm=pm, image=image, command=command,
                    )),
                    HumanMessage(content="Generate the lock file now."),
                ]
            },
            config={"recursion_limit": 25},
        )
        output: LockGenResult = result["structured_response"]
    except Exception as exc:
        logger.exception("lock_generator_agent: failed")
        return {
            "lock_generation_attempts": 0,
            "lock_generation_error": f"Lock generator agent failed: {exc}",
        }

    return {
        "lock_generation_attempts": output.attempts,
        "lock_generation_error": output.error if not output.success else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/subgraphs/discovery/test_lock_generator_agent.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/lock_generator_agent.py \
        tests/unit/subgraphs/discovery/test_lock_generator_agent.py
git commit -m "feat(discovery): add lock_generator_agent with docker + file tools"
```

---

## Task 5: Update build_dependency_summary

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`

- [ ] **Step 1: Update `_build_prompt` to accept a lock note**

Open `src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py`.

Change the `_build_prompt` signature and body to include an optional note:

```python
def _build_prompt(
    project_name: str,
    concern: str,
    pm: str,
    direct_names: list[str],
    direct_count: int,
    total_count: int,
    transitive_count: int,
    lock_note: str = "",
) -> str:
    def _fmt(names: list[str], limit: int = 20) -> str:
        items = names[:limit]
        result = ", ".join(items)
        if len(names) > limit:
            result += f", ... and {len(names) - limit} more"
        return result or "none"

    return f"""\
You are analyzing the dependency structure of a Node.js project.

Project: {project_name}
Package manager: {pm}
Direct dependencies ({direct_count}): {_fmt(direct_names)}
Transitive dependencies: {transitive_count}
Total components: {total_count}
Analysis concern: {concern}{lock_note}

Write a concise 3-paragraph summary (no headers, plain prose, ~150 words total):
1. Ecosystem profile: package manager, dependency counts, notable packages.
2. Concern relevance: which packages matter most for "{concern}" and why.
3. Risk signals: version pinning, transitive depth, dev-in-prod, or other flags visible from the SBOM.

Output only the summary text.\
"""
```

- [ ] **Step 2: Pass lock_note from state in `build_dependency_summary`**

In the same file, update the call inside `build_dependency_summary`:

```python
    lock_note = ""
    if state.get("lock_generation_error"):
        lock_note = f"\nNote: lock file generation failed ({state['lock_generation_error']}); transitive dependencies may be incomplete."

    prompt = _build_prompt(
        project_name, concern, pm, direct_names,
        len(direct_refs), len(components), transitive_count,
        lock_note=lock_note,
    )
```

- [ ] **Step 3: Run existing tests to confirm nothing broke**

```bash
uv run pytest tests/unit/subgraphs/discovery/test_build_dependency_summary.py -v
```

Expected: all existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/build_dependency_summary.py
git commit -m "feat(discovery): surface lock_generation_error in dependency summary prompt"
```

---

## Task 6: Update nodes/__init__.py

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/nodes/__init__.py`

- [ ] **Step 1: Replace imports**

Replace the full file content:

```python
from src.main_graph.subgraphs.discovery.nodes.build_dependency_summary import (
    build_dependency_summary,
)
from src.main_graph.subgraphs.discovery.nodes.clone_repository import (
    clone_repository,
)
from src.main_graph.subgraphs.discovery.nodes.generate_sbom import (
    generate_sbom,
)
from src.main_graph.subgraphs.discovery.nodes.inspector_agent import (
    inspector_agent,
)
from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import (
    lock_generator_agent,
)

__all__ = [
    "clone_repository",
    "inspector_agent",
    "lock_generator_agent",
    "generate_sbom",
    "build_dependency_summary",
]
```

- [ ] **Step 2: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/__init__.py
git commit -m "chore(discovery): update nodes __init__ imports"
```

---

## Task 7: Rewire graph.py

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/graph.py`

- [ ] **Step 1: Replace graph.py**

Replace the full file content:

```python
"""Discovery subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    CLONE_REPOSITORY,
    GENERATE_SBOM,
    INSPECTOR_AGENT,
    LOCK_GENERATOR_AGENT,
)
from src.main_graph.subgraphs.discovery.nodes import (
    build_dependency_summary,
    clone_repository,
    generate_sbom,
    inspector_agent,
    lock_generator_agent,
)
from src.main_graph.subgraphs.discovery.state import DiscoveryState


def _after_clone(state: DiscoveryState) -> str:
    return BUILD_DEPENDENCY_SUMMARY if state.get("discovery_error") else INSPECTOR_AGENT


def _after_inspector(state: DiscoveryState) -> str:
    if state.get("discovery_error"):
        return BUILD_DEPENDENCY_SUMMARY
    if state.get("lock_file_missing"):
        return LOCK_GENERATOR_AGENT
    return GENERATE_SBOM


def build_discovery_subgraph() -> StateGraph:
    builder = StateGraph(DiscoveryState)

    builder.add_node(CLONE_REPOSITORY, clone_repository)
    builder.add_node(INSPECTOR_AGENT, inspector_agent)
    builder.add_node(LOCK_GENERATOR_AGENT, lock_generator_agent)
    builder.add_node(GENERATE_SBOM, generate_sbom)
    builder.add_node(BUILD_DEPENDENCY_SUMMARY, build_dependency_summary)

    builder.add_edge(START, CLONE_REPOSITORY)
    builder.add_conditional_edges(
        CLONE_REPOSITORY, _after_clone, [INSPECTOR_AGENT, BUILD_DEPENDENCY_SUMMARY]
    )
    builder.add_conditional_edges(
        INSPECTOR_AGENT, _after_inspector,
        [LOCK_GENERATOR_AGENT, GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY]
    )
    builder.add_edge(LOCK_GENERATOR_AGENT, GENERATE_SBOM)
    builder.add_edge(GENERATE_SBOM, BUILD_DEPENDENCY_SUMMARY)
    builder.add_edge(BUILD_DEPENDENCY_SUMMARY, END)

    return builder.compile()


discovery_subgraph = build_discovery_subgraph()
```

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest tests/unit/ -v
```

Expected: all tests PASS. Fix any import errors before proceeding.

- [ ] **Step 3: Smoke-test the subgraph compiles**

```bash
uv run python -c "from src.main_graph.subgraphs.discovery.graph import discovery_subgraph; print('ok')"
```

Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/main_graph/subgraphs/discovery/graph.py
git commit -m "feat(discovery): rewire graph to 5-node agentic pipeline"
```

---

## Task 8: Update debug script

**Files:**
- Modify: `scripts/debug_subgraphs.py`

- [ ] **Step 1: Update the `JOB_ID` line and import reference**

In `scripts/debug_subgraphs.py`, the `JOB_ID` currently uses `asyncio.get_event_loop().time()` which produces a float. Change to:

```python
import time
JOB_ID = str(int(time.time()))
```

Remove the `asyncio.get_event_loop().time()` line (it triggered a deprecation warning in Python 3.12).

- [ ] **Step 2: Run the debug script in discovery mode to verify end-to-end**

```bash
uv run python scripts/debug_subgraphs.py discovery
```

Expected: script runs without import errors; clone + inspector + (optional lock gen) + sbom + summary complete. Actual LLM + Docker calls will run — ensure Docker is available and env vars are set.

- [ ] **Step 3: Commit**

```bash
git add scripts/debug_subgraphs.py
git commit -m "chore: fix JOB_ID deprecation in debug script"
```
