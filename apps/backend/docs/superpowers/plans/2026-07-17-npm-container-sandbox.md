# npm Tool Container Sandboxing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the conductor's `npm_list`, `npm_audit`, `npm_outdated`, and `resolve_transitive_parent` tools through the existing `ContainerRunPort`/`DockerContainerAdapter` instead of shelling out to `npm` on the host, so every npm invocation against a cloned (untrusted) third-party repo is sandboxed the same way `git clone` and `npm install` already are.

**Architecture:** `apps/backend/src/main_graph/tools/npm_cli.py` currently runs `npm` directly via `asyncio.create_subprocess_exec` on the host. We change its `_run_npm` helper to call `ContainerRunPort.run()` (the same port `install_deps.py` already uses), and extend `tool_runner.py`'s existing signature-based kwarg injection (it already injects `repo_path` into any tool that declares it) to also inject `container` and `docker_image`. `docker_image` already flows out of the discovery subgraph (`DiscoveryState.docker_image`, set in `inspect_repo.py`) but isn't declared on `MainState`, so it never reaches the conductor loop — we add it there. `conductor.py`'s tool-description formatter, which shows tool signatures to the LLM, must also stop leaking the two new injected params into the prompt.

**Tech Stack:** Python, LangGraph, pytest (`asyncio_mode = "auto"`), `unittest.mock.AsyncMock`.

## Global Constraints

- Package manager: `uv` — every command below is `uv run pytest ...`, run from `apps/backend/`.
- No behavior change to which npm subcommands run or their flags — only where they execute (container vs host).
- `container` has no safe default (it's a live port instance) — it must be a required parameter wherever it's injected. `docker_image` keeps the same fallback default used by `install_deps.py`: `"node:lts-alpine"`.
- Out of scope (explicitly deferred, do not attempt): batching multiple npm subcommands into a single container run to amortize startup overhead. Each tool call gets its own `container.run()`, exactly mirroring `install_deps.py`'s one-call-per-command style. If per-call container startup overhead becomes a real problem, that's a separate follow-up plan.

---

### Task 1: Add `docker_image` to `MainState`

**Files:**
- Modify: `apps/backend/src/main_graph/state.py`

**Interfaces:**
- Produces: `MainState["docker_image"]` (`NotRequired[str]`) — readable by any node downstream of `prep`, same pattern as the existing `detected_package_manager` field.

- [ ] **Step 1: Add the field**

In `apps/backend/src/main_graph/state.py`, add `docker_image` next to `detected_package_manager` in the "Prep outputs" section:

```python
    # Prep outputs
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    docker_image: NotRequired[str]
    project_context: NotRequired[str]
    discovery_error: NotRequired[str | None]
```

- [ ] **Step 2: Verify it doesn't break anything**

Run: `uv run pytest tests/unit -q` (from `apps/backend/`)
Expected: same pass/fail counts as before this change (a `TypedDict` field addition has no runtime effect by itself).

- [ ] **Step 3: Commit**

```bash
git add apps/backend/src/main_graph/state.py
git commit -m "feat: propagate docker_image from discovery into MainState"
```

---

### Task 2: Inject `container` and `docker_image` into tool kwargs in `tool_runner.py`

**Files:**
- Modify: `apps/backend/src/main_graph/nodes/tool_runner.py`
- Test: `apps/backend/tests/unit/nodes/test_tool_runner.py`

**Interfaces:**
- Consumes: `get_services(config)` from `src.main_graph.config` (already returns `PipelineConfigurable` with a mandatory `"container": ContainerRunPort` key, wired in `job_runner.py::_build_config`); `MainState["docker_image"]` from Task 1.
- Produces: `_run_tool(tc, repo_path, container, docker_image)` — any registered tool whose signature declares a `container` and/or `docker_image` parameter now receives them automatically, the same way `repo_path` is already injected.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `apps/backend/tests/unit/nodes/test_tool_runner.py` with:

```python
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.nodes.tool_runner import tool_runner
from src.models.conductor import ConductorDecision, ToolCall, ToolResult


def _make_state(tool_calls: list[ToolCall], repo_path: str = "/tmp/repo", docker_image: str | None = None):
    decision = ConductorDecision(
        tool_calls=tool_calls, findings=[], ask_user=None,
        checkpoint_message=None, finalize=False, reasoning="r",
    )
    state = {
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
    if docker_image is not None:
        state["docker_image"] = docker_image
    return state


def _config(container=None):
    return {"configurable": {"container": container or AsyncMock()}}


@pytest.mark.asyncio
async def test_tool_runner_executes_registered_tool():
    fake_output = {"deps": {"lodash": "4.17.21"}}
    tc = ToolCall(tool="npm_list", args={}, reason="check deps")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"npm_list": AsyncMock(return_value=fake_output)}):
        result = await tool_runner(_make_state([tc]), config=_config())
    assert len(result["tool_results"]) == 1
    tr: ToolResult = result["tool_results"][0]
    assert tr.tool == "npm_list"
    assert tr.output == fake_output
    assert tr.error is None


@pytest.mark.asyncio
async def test_tool_runner_captures_error_for_unknown_tool():
    tc = ToolCall(tool="nonexistent_tool", args={}, reason="test")
    result = await tool_runner(_make_state([tc]), config=_config())
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
        result = await tool_runner(_make_state(tcs), config=_config())
        elapsed = time.monotonic() - start
    assert len(result["tool_results"]) == 2
    # Parallel execution should be ~50ms, not ~100ms
    assert elapsed < 0.08, f"tools ran sequentially: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_tool_runner_sets_duration_ms():
    tc = ToolCall(tool="npm_list", args={}, reason="check")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"npm_list": AsyncMock(return_value={})}):
        result = await tool_runner(_make_state([tc]), config=_config())
    assert result["tool_results"][0].duration_ms >= 0


@pytest.mark.asyncio
async def test_tool_runner_does_not_inject_repo_path_for_tools_without_it():
    """Tools that don't declare repo_path should not receive it as a kwarg."""
    received_kwargs: dict = {}

    async def no_repo_tool(package_name: str) -> dict:
        received_kwargs["package_name"] = package_name
        return {"ok": True}

    tc = ToolCall(tool="no_repo_tool", args={"package_name": "lodash"}, reason="test")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"no_repo_tool": no_repo_tool}):
        result = await tool_runner(_make_state([tc]), config=_config())
    assert result["tool_results"][0].error is None
    assert received_kwargs == {"package_name": "lodash"}


@pytest.mark.asyncio
async def test_tool_runner_injects_repo_path_for_tools_that_declare_it():
    """Tools that declare repo_path should receive it."""
    received_kwargs: dict = {}

    async def file_tool(repo_path: str, extra: str = "") -> dict:
        received_kwargs["repo_path"] = repo_path
        return {"ok": True}

    tc = ToolCall(tool="file_tool", args={}, reason="test")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"file_tool": file_tool}):
        result = await tool_runner(_make_state([tc], repo_path="/tmp/myrepo"), config=_config())
    assert result["tool_results"][0].error is None
    assert received_kwargs["repo_path"] == "/tmp/myrepo"


@pytest.mark.asyncio
async def test_tool_runner_injects_container_for_tools_that_declare_it():
    """Tools that declare container should receive the ContainerRunPort from config."""
    received_kwargs: dict = {}

    async def container_tool(container) -> dict:
        received_kwargs["container"] = container
        return {"ok": True}

    fake_container = AsyncMock()
    tc = ToolCall(tool="container_tool", args={}, reason="test")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"container_tool": container_tool}):
        result = await tool_runner(_make_state([tc]), config=_config(container=fake_container))
    assert result["tool_results"][0].error is None
    assert received_kwargs["container"] is fake_container


@pytest.mark.asyncio
async def test_tool_runner_injects_docker_image_with_default():
    """Tools that declare docker_image get the state value, or the fallback default if unset."""
    received_kwargs: dict = {}

    async def image_tool(docker_image: str) -> dict:
        received_kwargs["docker_image"] = docker_image
        return {"ok": True}

    tc = ToolCall(tool="image_tool", args={}, reason="test")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"image_tool": image_tool}):
        result = await tool_runner(_make_state([tc]), config=_config())
    assert result["tool_results"][0].error is None
    assert received_kwargs["docker_image"] == "node:lts-alpine"


@pytest.mark.asyncio
async def test_tool_runner_injects_docker_image_from_state():
    received_kwargs: dict = {}

    async def image_tool(docker_image: str) -> dict:
        received_kwargs["docker_image"] = docker_image
        return {"ok": True}

    tc = ToolCall(tool="image_tool", args={}, reason="test")
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"image_tool": image_tool}):
        result = await tool_runner(
            _make_state([tc], docker_image="node:22-alpine"), config=_config()
        )
    assert received_kwargs["docker_image"] == "node:22-alpine"
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/unit/nodes/test_tool_runner.py -v` (from `apps/backend/`)
Expected: `test_tool_runner_injects_container_for_tools_that_declare_it` and the two `docker_image` tests FAIL (container/docker_image not injected yet); the pre-existing tests still PASS since `config=_config()` is a superset of what they needed before (an empty `{}` also worked pre-change, but this confirms the new config shape doesn't regress them).

- [ ] **Step 3: Implement the injection**

Replace the full contents of `apps/backend/src/main_graph/nodes/tool_runner.py` with:

```python
"""Tool runner node — executes conductor tool calls in parallel."""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid

from langchain_core.runnables import RunnableConfig

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.config import get_services
from src.main_graph.state import MainState
from src.main_graph.tools.registry import TOOL_REGISTRY
from src.models.conductor import ToolCall, ToolResult

logger = logging.getLogger(__name__)


async def _run_tool(
    tc: ToolCall, repo_path: str, container: ContainerRunPort, docker_image: str
) -> ToolResult:
    start = time.monotonic()
    fn = TOOL_REGISTRY.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=f"tool '{tc.tool}' not found in registry",
            duration_ms=0,
        )
    try:
        sig = inspect.signature(fn)
        kwargs = dict(tc.args)
        if "repo_path" in sig.parameters:
            kwargs["repo_path"] = repo_path
        if "container" in sig.parameters:
            kwargs["container"] = container
        if "docker_image" in sig.parameters:
            kwargs["docker_image"] = docker_image
        output = await fn(**kwargs)
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
    docker_image = state.get("docker_image", "node:lts-alpine")
    container = get_services(config)["container"]
    tool_calls = decision.tool_calls

    logger.info("tool_runner: executing %d tools in parallel", len(tool_calls))
    results = await asyncio.gather(
        *[_run_tool(tc, repo_path, container, docker_image) for tc in tool_calls]
    )

    for tr in results:
        if tr.error:
            logger.warning("tool_runner: tool=%s error=%s", tr.tool, tr.error)
        else:
            logger.info("tool_runner: tool=%s duration_ms=%d", tr.tool, tr.duration_ms)

    return {"tool_results": list(results)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/nodes/test_tool_runner.py -v` (from `apps/backend/`)
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/nodes/tool_runner.py apps/backend/tests/unit/nodes/test_tool_runner.py
git commit -m "feat: inject container and docker_image into tool kwargs by signature"
```

---

### Task 3: Route `npm_cli.py` through `ContainerRunPort`

**Files:**
- Modify: `apps/backend/src/main_graph/tools/npm_cli.py`
- Test: `apps/backend/tests/unit/tools/test_npm_cli.py`

**Interfaces:**
- Consumes: `container: ContainerRunPort` and `docker_image: str` injected by `tool_runner.py` (Task 2). `ContainerRunPort.run(image, command, volume=None, run_as_root=False) -> (returncode, stdout, stderr)`, defined in `src/domain/ports/container_run_port.py`, implemented by `DockerContainerAdapter` in `src/main_graph/adapters/docker_container_adapter.py`.
- Produces: `npm_list(repo_path, container, docker_image="node:lts-alpine") -> dict`, `npm_audit(repo_path, container, docker_image="node:lts-alpine") -> dict`, `npm_outdated(repo_path, container, docker_image="node:lts-alpine") -> dict`, `resolve_transitive_parent(repo_path, package_name, container, docker_image="node:lts-alpine") -> dict` — same return shapes as before, just resolved by running inside a container.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `apps/backend/tests/unit/tools/test_npm_cli.py` with:

```python
import json
from unittest.mock import AsyncMock

import pytest

import src.main_graph.tools.npm_cli  # trigger registration
from src.main_graph.tools.registry import TOOL_REGISTRY


def _container(stdout: str = "", stderr: str = "", rc: int = 0) -> AsyncMock:
    container = AsyncMock()
    container.run.return_value = (rc, stdout, stderr)
    return container


@pytest.mark.asyncio
async def test_npm_list_runs_inside_container():
    fake_output = '{"version": "1.0.0", "dependencies": {"lodash": {"version": "4.17.21"}}}'
    container = _container(stdout=fake_output)
    result = await TOOL_REGISTRY["npm_list"](
        repo_path="/tmp/repo", container=container, docker_image="node:lts-alpine"
    )
    assert result["dependencies"]["lodash"]["version"] == "4.17.21"
    container.run.assert_awaited_once()
    _, kwargs = container.run.call_args
    assert kwargs["image"] == "node:lts-alpine"
    assert kwargs["volume"] == "/tmp/repo:/workspace"
    assert "npm list --json --all" in kwargs["command"]


@pytest.mark.asyncio
async def test_npm_list_returns_error_on_failure():
    container = AsyncMock()
    container.run.side_effect = Exception("container failed")
    result = await TOOL_REGISTRY["npm_list"](
        repo_path="/tmp/repo", container=container, docker_image="node:lts-alpine"
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_npm_audit_parses_vulnerabilities():
    fake_output = '{"vulnerabilities": {"lodash": {"severity": "high", "name": "lodash"}}, "metadata": {"vulnerabilities": {"high": 1}}}'
    container = _container(stdout=fake_output)
    result = await TOOL_REGISTRY["npm_audit"](
        repo_path="/tmp/repo", container=container, docker_image="node:lts-alpine"
    )
    assert result["metadata"]["vulnerabilities"]["high"] == 1


@pytest.mark.asyncio
async def test_npm_outdated_parses_output():
    fake_output = '{"lodash": {"current": "4.17.20", "latest": "4.17.21", "wanted": "4.17.21"}}'
    container = _container(stdout=fake_output)
    result = await TOOL_REGISTRY["npm_outdated"](
        repo_path="/tmp/repo", container=container, docker_image="node:lts-alpine"
    )
    assert "lodash" in result["outdated"]


def test_tools_are_registered():
    for name in ("npm_list", "npm_audit", "npm_outdated"):
        assert name in TOOL_REGISTRY


@pytest.fixture
def repo_with_pkg(tmp_path):
    pkg = {
        "name": "my-app",
        "dependencies": {"express": "^4.18.0"},
        "devDependencies": {"jest": "^29.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    return str(tmp_path)


@pytest.mark.asyncio
async def test_resolve_transitive_parent_direct_dep(repo_with_pkg):
    """express is a direct dep — is_direct should be True, and no container run is needed."""
    container = _container(stdout="{}")
    result = await TOOL_REGISTRY["resolve_transitive_parent"](
        repo_path=repo_with_pkg, package_name="express",
        container=container, docker_image="node:lts-alpine",
    )
    assert result["is_direct"] is True
    assert result["brought_in_by"] == []
    container.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_transitive_parent_transitive_dep(repo_with_pkg):
    """accepts is a transitive dep brought in by express."""
    npm_tree = json.dumps({
        "name": "my-app",
        "dependencies": {
            "express": {
                "version": "4.18.2",
                "dependencies": {
                    "accepts": {"version": "1.3.8", "dependencies": {}}
                }
            }
        }
    })
    container = _container(stdout=npm_tree)
    result = await TOOL_REGISTRY["resolve_transitive_parent"](
        repo_path=repo_with_pkg, package_name="accepts",
        container=container, docker_image="node:lts-alpine",
    )
    assert result["is_direct"] is False
    assert "express" in result["brought_in_by"]


@pytest.mark.asyncio
async def test_resolve_transitive_parent_unknown_package(repo_with_pkg):
    """Package not found anywhere returns empty parents."""
    npm_tree = json.dumps({"name": "my-app", "dependencies": {}})
    container = _container(stdout=npm_tree)
    result = await TOOL_REGISTRY["resolve_transitive_parent"](
        repo_path=repo_with_pkg, package_name="ghost-package",
        container=container, docker_image="node:lts-alpine",
    )
    assert result["is_direct"] is False
    assert result["brought_in_by"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/tools/test_npm_cli.py -v` (from `apps/backend/`)
Expected: FAIL — current tool signatures don't accept `container`/`docker_image` kwargs (`TypeError: npm_list() got an unexpected keyword argument 'container'`).

- [ ] **Step 3: Implement the container-backed `_run_npm`**

Replace the full contents of `apps/backend/src/main_graph/tools/npm_cli.py` with:

```python
"""npm tools executed inside a sandboxed container: npm_list, npm_audit, npm_outdated."""
from __future__ import annotations

import json
import logging
import os

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.tools.registry import register

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = "node:lts-alpine"


async def _run_npm(
    args: list[str], repo_path: str, container: ContainerRunPort, docker_image: str
) -> tuple[str, str]:
    command = "cd /workspace && npm " + " ".join(args)
    volume = f"{repo_path}:/workspace"
    _rc, stdout, stderr = await container.run(
        image=docker_image, command=command, volume=volume, run_as_root=True
    )
    return stdout, stderr


def _safe_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


@register("npm_list", "Runs `npm list --json`; returns full dependency tree with installed versions")
async def npm_list(repo_path: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE) -> dict:
    try:
        stdout, _ = await _run_npm(["list", "--json", "--all"], repo_path, container, docker_image)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_list failed: %s", exc)
        return {"error": str(exc)}


@register("npm_audit", "Runs `npm audit --json`; returns vulnerabilities, severities, and affected packages")
async def npm_audit(repo_path: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE) -> dict:
    try:
        stdout, _ = await _run_npm(["audit", "--json"], repo_path, container, docker_image)
        return _safe_json(stdout)
    except Exception as exc:
        logger.warning("npm_audit failed: %s", exc)
        return {"error": str(exc)}


@register("npm_outdated", "Returns packages with newer versions available via `npm outdated --json`")
async def npm_outdated(repo_path: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE) -> dict:
    try:
        stdout, _ = await _run_npm(["outdated", "--json"], repo_path, container, docker_image)
        data = _safe_json(stdout)
        return {"outdated": data}
    except Exception as exc:
        logger.warning("npm_outdated failed: %s", exc)
        return {"error": str(exc)}


def _in_subtree(deps: dict, target: str) -> bool:
    """Return True if target package exists anywhere in the deps subtree."""
    if target in deps:
        return True
    return any(_in_subtree(info.get("dependencies") or {}, target) for info in deps.values())


def _find_chain(deps: dict, target: str, prefix: str = "") -> str:
    """Return first dep_chain string that reaches target, or 'unknown'."""
    for name, info in deps.items():
        current = f"{prefix} → {name}" if prefix else name
        sub = info.get("dependencies") or {}
        if target in sub:
            return f"{current} → {target}"
        result = _find_chain(sub, target, current)
        if result != "unknown":
            return result
    return "unknown"


@register(
    "resolve_transitive_parent",
    "Determines if a package is a direct or transitive dependency and identifies which direct deps bring it in",
)
async def resolve_transitive_parent(
    repo_path: str, package_name: str, container: ContainerRunPort, docker_image: str = _DEFAULT_IMAGE
) -> dict:
    try:
        pkg_path = os.path.join(repo_path, "package.json")
        with open(pkg_path) as f:
            pkg = json.load(f)
        direct_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

        if package_name in direct_deps:
            return {
                "package": package_name,
                "is_direct": True,
                "brought_in_by": [],
                "dep_chain": package_name,
            }

        stdout, _ = await _run_npm(["ls", "--json", "--all"], repo_path, container, docker_image)
        tree = _safe_json(stdout)
        tree_deps = tree.get("dependencies") or {}

        parents = [name for name, info in tree_deps.items()
                   if _in_subtree(info.get("dependencies") or {}, package_name)]

        return {
            "package": package_name,
            "is_direct": False,
            "brought_in_by": parents,
            "dep_chain": _find_chain(tree_deps, package_name),
        }
    except Exception as exc:
        logger.warning("resolve_transitive_parent failed: %s", exc)
        return {"error": str(exc), "package": package_name}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/tools/test_npm_cli.py -v` (from `apps/backend/`)
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/tools/npm_cli.py apps/backend/tests/unit/tools/test_npm_cli.py
git commit -m "feat: run npm_cli tools inside a sandboxed container via ContainerRunPort"
```

---

### Task 4: Stop leaking `container`/`docker_image` into the LLM-facing tool signature

**Files:**
- Modify: `apps/backend/src/main_graph/nodes/conductor.py`
- Test: `apps/backend/tests/unit/nodes/test_conductor.py`

**Interfaces:**
- Consumes: `TOOL_REGISTRY` from `src.main_graph.tools.registry` (populated by Task 3's `npm_cli.py`, which now declares `container`/`docker_image` params on its registered tools).
- Produces: `_tool_signature(name: str) -> str` — unchanged public behavior except it now also excludes `container` and `docker_image`, same as it already excludes `repo_path`.

**Context:** `conductor.py::_tool_signature` builds the tool descriptions shown to the LLM in the system prompt (`_format_tool_descriptions`, used in `_SYSTEM`). It already strips `repo_path` since that's injected by `tool_runner.py`, not chosen by the LLM. After Task 3, `npm_list`/`npm_audit`/`npm_outdated`/`resolve_transitive_parent` also declare `container: ContainerRunPort` and `docker_image: str = "node:lts-alpine"` — without this fix, the LLM would see nonsense signatures like `npm_list(container: ContainerRunPort, docker_image: str = 'node:lts-alpine')` and might try to pass those as tool-call arguments.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/nodes/test_conductor.py` (append at the end of the file):

```python
def test_tool_signature_excludes_injected_params():
    import src.main_graph.tools.npm_cli  # noqa: F401 — trigger registration
    from src.main_graph.nodes.conductor import _tool_signature

    sig = _tool_signature("npm_list")
    assert "container" not in sig
    assert "docker_image" not in sig
    assert "repo_path" not in sig


def test_tool_signature_keeps_llm_facing_params():
    import src.main_graph.tools.npm_cli  # noqa: F401 — trigger registration
    from src.main_graph.nodes.conductor import _tool_signature

    sig = _tool_signature("resolve_transitive_parent")
    assert "package_name" in sig
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `uv run pytest tests/unit/nodes/test_conductor.py -v` (from `apps/backend/`)
Expected: `test_tool_signature_excludes_injected_params` FAILS (`container`/`docker_image` currently show up in the signature string); `test_tool_signature_keeps_llm_facing_params` PASSES already.

- [ ] **Step 3: Implement the fix**

In `apps/backend/src/main_graph/nodes/conductor.py`, find the current `_tool_signature` function:

```python
def _tool_signature(name: str) -> str:
    """Return 'name(param: type, ...)' — excludes injected repo_path."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return name
    params = [
        str(p)
        for k, p in inspect.signature(fn).parameters.items()
        if k != "repo_path"
    ]
    return f"{name}({', '.join(params)})"
```

Replace it with:

```python
_INJECTED_PARAMS = {"repo_path", "container", "docker_image"}


def _tool_signature(name: str) -> str:
    """Return 'name(param: type, ...)' — excludes params injected by tool_runner."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return name
    params = [
        str(p)
        for k, p in inspect.signature(fn).parameters.items()
        if k not in _INJECTED_PARAMS
    ]
    return f"{name}({', '.join(params)})"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/nodes/test_conductor.py -v` (from `apps/backend/`)
Expected: all PASS.

- [ ] **Step 5: Run the full backend test suite**

Run: `uv run pytest tests/unit -q` (from `apps/backend/`)
Expected: all PASS — this confirms Tasks 1-4 compose correctly (state field, injection, container-backed npm calls, and prompt formatting).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/nodes/conductor.py apps/backend/tests/unit/nodes/test_conductor.py
git commit -m "fix: exclude injected container/docker_image params from LLM tool signatures"
```

---

## Manual Verification (post-implementation)

Automated tests mock `ContainerRunPort`, so they don't prove Docker actually runs. Before considering this done, run one real analysis job locally (per `apps/backend/docs/development-setup.md`) against a small public repo and confirm in the logs:
- `docker_container_adapter.py`'s `logger.info("docker: %s", ...)` line fires for `npm list`, `npm audit`, and `npm outdated` during the conductor loop (not just during `install_deps`).
- No `asyncio.create_subprocess_exec("npm", ...)` host calls remain (grep the logs for a bare `npm` process, there should be none — every npm invocation goes through `docker run`).
- The report produced is materially the same as before this change (same dependency tree, same vulnerabilities surfaced), confirming no behavior regression from moving execution into the container.
