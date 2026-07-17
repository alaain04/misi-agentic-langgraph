# Hexagonal HTTP Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct `httpx.AsyncClient()` instantiation in `main_graph/tools/external_api.py` with a `HttpClientPort`/`HttpxClientAdapter` pair, injected the same way `ContainerRunPort` already is, closing the hexagonal-architecture gap identified for outbound HTTP calls.

**Architecture:** One generic port (`get`/`post`) in `src/domain/ports/`, one adapter in `src/main_graph/adapters/`, wired into `PipelineConfigurable` and assembled in `job_runner._build_config`. `main_graph/tools/external_api.py`'s registered tool functions reach it via `get_services(config)["http"]`, with `config` threaded into them by extending `tool_runner.py`'s existing signature-introspection injection (the same mechanism that already injects `repo_path`).

**Tech Stack:** Python 3.12, LangGraph, httpx, pytest + pytest-asyncio (`asyncio_mode = "auto"`), `uv` as package manager.

## Global Constraints

- Spec: `apps/backend/docs/superpowers/specs/2026-07-15-hexagonal-http-port-design.md`.
- `HttpClientPort` is generic (`get`/`post`), not one port per external API — matches the existing `ContainerRunPort` convention of a generic capability rather than e.g. `RunNpmAuditPort`.
- `HttpxClientAdapter` uses `timeout=10.0`, matching today's `_TIMEOUT` in `external_api.py`. No new exception types — `httpx` exceptions propagate unchanged; every call site already catches broad `Exception` and returns `{"error": str(exc)}`.
- **Non-goals, do not touch:** `LLMPort` / `utils/llm.py` / `conductor.py` / `report_builder.py` / `subgraphs/discovery/nodes/build_dependency_summary.py` (explicit prior team decision: LLM is "the core execution engine, not peripheral infrastructure," reaffirmed in the spec). `main_graph/tools/npm_cli.py` (deferred — containerizing it is a runtime-behavior change, separate decision).
- Adapters are not directly unit-tested in this codebase (`DockerContainerAdapter` has zero dedicated tests) — follow that precedent. Coverage comes from consumer-side tests that pass a fake/mock `HttpClientPort`.
- Run all commands from `apps/backend/` using `uv run pytest ...` (per `Makefile`'s `test` target).
- Commits: conventional commit messages, no AI attribution trailer.

---

### Task 1: `HttpClientPort` + `HttpxClientAdapter`, wired into `PipelineConfigurable`

**Files:**
- Create: `src/domain/ports/http_client_port.py`
- Create: `src/main_graph/adapters/httpx_client_adapter.py`
- Modify: `src/main_graph/config.py`
- Modify: `src/services/job_runner.py:22-32` (`_build_config`)
- Test: `tests/unit/services/test_job_runner.py`

**Interfaces:**
- Produces: `HttpClientPort` (ABC) with `async get(url: str, headers: dict | None = None, params: dict | None = None) -> dict` and `async post(url: str, json: dict | None = None, headers: dict | None = None) -> dict`.
- Produces: `HttpxClientAdapter(HttpClientPort)` in `src/main_graph/adapters/httpx_client_adapter.py`, no-arg constructor.
- Produces: `PipelineConfigurable["http"]` key of type `HttpClientPort`, populated by `_build_config`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/services/test_job_runner.py` (new imports at top, new test at the end):

```python
from src.domain.ports.http_client_port import HttpClientPort
from src.services.job_runner import _build_config
from src.utils.cost import CostCallback
```

```python
def test_build_config_includes_http_port():
    config = _build_config("job-1", _make_dao(), CostCallback())
    assert isinstance(config["configurable"]["http"], HttpClientPort)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_runner.py::test_build_config_includes_http_port -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.domain.ports.http_client_port'`

- [ ] **Step 3: Create the port**

`src/domain/ports/http_client_port.py`:

```python
from abc import ABC, abstractmethod


class HttpClientPort(ABC):
    @abstractmethod
    async def get(
        self, url: str, headers: dict | None = None, params: dict | None = None
    ) -> dict:
        """GET a URL, raise on non-2xx, return parsed JSON body."""
        ...

    @abstractmethod
    async def post(
        self, url: str, json: dict | None = None, headers: dict | None = None
    ) -> dict:
        """POST a URL, raise on non-2xx, return parsed JSON body."""
        ...
```

- [ ] **Step 4: Create the adapter**

`src/main_graph/adapters/httpx_client_adapter.py`:

```python
import httpx

from src.domain.ports.http_client_port import HttpClientPort

_TIMEOUT = 10.0


class HttpxClientAdapter(HttpClientPort):
    async def get(
        self, url: str, headers: dict | None = None, params: dict | None = None
    ) -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=headers or {}, params=params or {})
            r.raise_for_status()
            return r.json()

    async def post(
        self, url: str, json: dict | None = None, headers: dict | None = None
    ) -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=json or {}, headers=headers or {})
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 5: Wire `http` into `PipelineConfigurable`**

In `src/main_graph/config.py`, add the import and the key:

```python
from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.http_client_port import HttpClientPort
from src.domain.ports.job_repository_port import JobRepositoryPort


class PipelineConfigurable(TypedDict):
    job_repo: JobRepositoryPort
    container: ContainerRunPort
    http: HttpClientPort
    docker_tool: BaseTool
```

- [ ] **Step 6: Assemble it in `_build_config`**

In `src/services/job_runner.py`, add the import at the top:

```python
from src.main_graph.adapters.httpx_client_adapter import HttpxClientAdapter
```

Replace `_build_config` (currently `src/services/job_runner.py:22-32`):

```python
def _build_config(job_id: str, dao: JobRepositoryPort, cost_cb: CostCallback) -> dict:
    container = DockerContainerAdapter()
    return {
        "configurable": {
            "thread_id": job_id,
            "job_repo": dao,
            "container": container,
            "http": HttpxClientAdapter(),
            "docker_tool": make_docker_tool(container),
        },
        "callbacks": [cost_cb],
    }
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_runner.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 8: Commit**

```bash
git add src/domain/ports/http_client_port.py src/main_graph/adapters/httpx_client_adapter.py src/main_graph/config.py src/services/job_runner.py tests/unit/services/test_job_runner.py
git commit -m "feat: add HttpClientPort and wire HttpxClientAdapter into pipeline config"
```

---

### Task 2: Thread `config` into `tool_runner` and inject it by signature

**Files:**
- Modify: `src/main_graph/nodes/tool_runner.py`
- Modify: `src/main_graph/tools/registry.py:1-5` (docstring)
- Test: `tests/unit/nodes/test_tool_runner.py`

**Interfaces:**
- Consumes: nothing new from Task 1.
- Produces: `_run_tool(tc: ToolCall, repo_path: str, config: RunnableConfig) -> ToolResult` — registered tool functions that declare a `config` parameter now receive the node's `RunnableConfig`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/nodes/test_tool_runner.py`, after `test_tool_runner_injects_repo_path_for_tools_that_declare_it`:

```python
@pytest.mark.asyncio
async def test_tool_runner_injects_config_for_tools_that_declare_it():
    """Tools that declare a `config` parameter should receive the node's RunnableConfig."""
    received_kwargs: dict = {}

    async def config_aware_tool(config, extra: str = "") -> dict:
        received_kwargs["config"] = config
        return {"ok": True}

    tc = ToolCall(tool="config_aware_tool", args={}, reason="test")
    sentinel_config = {"configurable": {"http": "fake-port"}}
    with patch("src.main_graph.nodes.tool_runner.TOOL_REGISTRY", {"config_aware_tool": config_aware_tool}):
        result = await tool_runner(_make_state([tc]), config=sentinel_config)
    assert result["tool_results"][0].error is None
    assert received_kwargs["config"] == sentinel_config
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/nodes/test_tool_runner.py::test_tool_runner_injects_config_for_tools_that_declare_it -v`
Expected: FAIL — `received_kwargs` stays empty because `config` is never passed to `config_aware_tool`, so the call raises `TypeError: config_aware_tool() missing 1 required positional argument: 'config'`, caught by `_run_tool`'s `except Exception`, making `tr.error` non-`None` and the `assert result["tool_results"][0].error is None` fail.

- [ ] **Step 3: Implement the injection**

In `src/main_graph/nodes/tool_runner.py`, change `_run_tool`'s signature and body:

```python
async def _run_tool(tc: ToolCall, repo_path: str, config: RunnableConfig) -> ToolResult:
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
        if "config" in sig.parameters:
            kwargs["config"] = config
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
```

And update `tool_runner` to pass `config` through:

```python
async def tool_runner(state: MainState, config: RunnableConfig) -> dict:
    decision = state.get("conductor_decision")
    if decision is None or not decision.tool_calls:
        return {"tool_results": []}

    repo_path = state.get("repo_path", "")
    tool_calls = decision.tool_calls

    logger.info("tool_runner: executing %d tools in parallel", len(tool_calls))
    results = await asyncio.gather(*[_run_tool(tc, repo_path, config) for tc in tool_calls])

    for tr in results:
        if tr.error:
            logger.warning("tool_runner: tool=%s error=%s", tr.tool, tr.error)
        else:
            logger.info("tool_runner: tool=%s duration_ms=%d", tr.tool, tr.duration_ms)

    return {"tool_results": list(results)}
```

- [ ] **Step 4: Update the registry contract docstring**

In `src/main_graph/tools/registry.py`, line 4, change:

```python
Each tool is: async (repo_path: str, **kwargs) -> dict
```

to:

```python
Each tool is: async (**kwargs) -> dict. Declare `repo_path: str` and/or
`config: RunnableConfig` as parameters to receive them via injection —
see tool_runner.py's signature-based dispatch.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/nodes/test_tool_runner.py -v`
Expected: PASS (all tests in the file — confirms the new injection works and existing `repo_path` injection and dispatch behavior are unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/nodes/tool_runner.py src/main_graph/tools/registry.py tests/unit/nodes/test_tool_runner.py
git commit -m "feat: inject RunnableConfig into registered tools that declare it"
```

---

### Task 3: Migrate `web_search` off raw `httpx`

**Files:**
- Modify: `src/main_graph/tools/external_api.py:1-18` (imports), `:240-258` (`web_search`)
- Test: `tests/unit/tools/test_web_search.py` (full rewrite)

**Interfaces:**
- Consumes: `get_services` from `src.main_graph.config` (Task 1), `HttpClientPort` from `src.domain.ports.http_client_port` (Task 1), `config` injection from Task 2.
- Produces: `web_search(query: str, config: RunnableConfig) -> dict` — same return shape as before (`{"query": ..., "results": [...]}` or `{"error": ..., "results": []}`).

- [ ] **Step 1: Write the failing test (full rewrite)**

Replace `tests/unit/tools/test_web_search.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

import src.main_graph.tools.external_api  # trigger registration
from src.domain.ports.http_client_port import HttpClientPort
from src.main_graph.tools.registry import TOOL_REGISTRY


def _config_with_http(http: AsyncMock) -> dict:
    return {"configurable": {"http": http}}


@pytest.mark.asyncio
async def test_web_search_returns_results():
    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.post.return_value = {
        "results": [
            {"title": "lodash alternative", "url": "https://example.com", "content": "Use ramda instead"}
        ]
    }

    with patch("src.main_graph.tools.external_api.settings") as mock_settings:
        mock_settings.tavily_api_key = "test-key"
        result = await TOOL_REGISTRY["web_search"](
            query="lodash alternatives npm", config=_config_with_http(fake_http)
        )

    assert result["query"] == "lodash alternatives npm"
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://example.com"
    fake_http.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_web_search_returns_error_when_no_api_key():
    with patch("src.main_graph.tools.external_api.settings") as mock_settings:
        mock_settings.tavily_api_key = ""
        result = await TOOL_REGISTRY["web_search"](query="test", config={"configurable": {}})
    assert "error" in result
    assert result["results"] == []


@pytest.mark.asyncio
async def test_web_search_handles_http_error():
    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.post.side_effect = Exception("connection refused")

    with patch("src.main_graph.tools.external_api.settings") as mock_settings:
        mock_settings.tavily_api_key = "test-key"
        result = await TOOL_REGISTRY["web_search"](
            query="test", config=_config_with_http(fake_http)
        )

    assert "error" in result
    assert result["results"] == []


def test_web_search_is_registered():
    assert "web_search" in TOOL_REGISTRY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_web_search.py -v`
Expected: FAIL — `web_search()` doesn't accept a `config` keyword argument yet, so every test raises `TypeError: web_search() got an unexpected keyword argument 'config'`.

- [ ] **Step 3: Add shared imports to `external_api.py`**

At the top of `src/main_graph/tools/external_api.py`, replace:

```python
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx

from src.main_graph.tools.package_files import _all_deps, _load_pkg
from src.main_graph.tools.registry import register
from src.utils.config import settings
```

with:

```python
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.tools.package_files import _all_deps, _load_pkg
from src.main_graph.tools.registry import register
from src.utils.config import settings
```

(`httpx` import removed — no longer used directly once all functions below are migrated in Tasks 3–6.)

- [ ] **Step 4: Migrate `web_search`**

Replace `web_search` (currently `src/main_graph/tools/external_api.py:240-258`):

```python
@register("web_search", "Searches the web for package alternatives, security advisories, or migration guides")
async def web_search(query: str, config: RunnableConfig) -> dict:
    if not settings.tavily_api_key:
        return {"error": "TAVILY_API_KEY not configured", "results": []}
    http = get_services(config)["http"]
    try:
        data = await http.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.tavily_api_key, "query": query, "max_results": 5},
        )
        results = [
            {"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("content", "")}
            for item in data.get("results", [])
        ]
        return {"query": query, "results": results}
    except Exception as exc:
        return {"error": str(exc), "results": []}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_web_search.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/tools/external_api.py tests/unit/tools/test_web_search.py
git commit -m "refactor: migrate web_search to HttpClientPort"
```

---

### Task 4: Migrate `github_advisory` off raw `httpx`

**Files:**
- Modify: `src/main_graph/tools/external_api.py:61-96`
- Test: `tests/unit/tools/test_github_advisory.py` (new)

**Interfaces:**
- Consumes: `get_services`, `RunnableConfig` (already imported in Task 3).
- Produces: `github_advisory(package_name: str, config: RunnableConfig, ecosystem: str = "NPM") -> dict` — same return shape as before.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/tools/test_github_advisory.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

import src.main_graph.tools.external_api  # trigger registration
from src.domain.ports.http_client_port import HttpClientPort
from src.main_graph.tools.external_api import clear_cache
from src.main_graph.tools.registry import TOOL_REGISTRY


def _config_with_http(http: AsyncMock) -> dict:
    return {"configurable": {"http": http}}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.mark.asyncio
async def test_github_advisory_returns_advisories():
    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.post.return_value = {
        "data": {
            "securityVulnerabilities": {
                "nodes": [{"severity": "HIGH", "advisory": {"summary": "bad thing"}}]
            }
        }
    }

    with patch("src.main_graph.tools.external_api.os.getenv", return_value="fake-token"):
        result = await TOOL_REGISTRY["github_advisory"](
            package_name="lodash", config=_config_with_http(fake_http)
        )

    assert result["count"] == 1
    assert result["advisories"][0]["severity"] == "HIGH"
    fake_http.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_github_advisory_returns_error_without_token():
    with patch("src.main_graph.tools.external_api.os.getenv", return_value=""):
        result = await TOOL_REGISTRY["github_advisory"](
            package_name="lodash", config=_config_with_http(AsyncMock(spec=HttpClientPort))
        )
    assert result["error"] == "GITHUB_TOKEN not set"
    assert result["advisories"] == []


@pytest.mark.asyncio
async def test_github_advisory_handles_http_error():
    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.post.side_effect = Exception("timeout")

    with patch("src.main_graph.tools.external_api.os.getenv", return_value="fake-token"):
        result = await TOOL_REGISTRY["github_advisory"](
            package_name="lodash", config=_config_with_http(fake_http)
        )

    assert "error" in result
    assert result["advisories"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_github_advisory.py -v`
Expected: FAIL — `TypeError: github_advisory() got an unexpected keyword argument 'config'`

- [ ] **Step 3: Migrate `github_advisory`**

Replace (currently `src/main_graph/tools/external_api.py:61-96`):

```python
@register("github_advisory", "Queries GitHub Advisory Database (GraphQL) for known vulnerabilities in a package")
async def github_advisory(package_name: str, config: RunnableConfig, ecosystem: str = "NPM") -> dict:
    key = f"advisory:{ecosystem}:{package_name}"
    if key in _cache:
        return _cache[key]
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
    http = get_services(config)["http"]
    try:
        data = await http.post(
            "https://api.github.com/graphql",
            json={"query": query, "variables": {"ecosystem": ecosystem, "package": package_name}},
            headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
        )
        nodes = data.get("data", {}).get("securityVulnerabilities", {}).get("nodes", [])
        result = {"package": package_name, "advisories": nodes, "count": len(nodes)}
        _cache[key] = result
        return result
    except Exception as exc:
        return {"error": str(exc), "advisories": []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_github_advisory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/tools/external_api.py tests/unit/tools/test_github_advisory.py
git commit -m "refactor: migrate github_advisory to HttpClientPort"
```

---

### Task 5: Migrate `osv_lookup` off raw `httpx`

**Files:**
- Modify: `src/main_graph/tools/external_api.py:99-117`
- Test: `tests/unit/tools/test_osv_lookup.py` (new)

**Interfaces:**
- Consumes: `get_services`, `RunnableConfig` (already imported).
- Produces: `osv_lookup(package_name: str, config: RunnableConfig, version: str = "", ecosystem: str = "npm") -> dict` — same return shape as before.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/tools/test_osv_lookup.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

import src.main_graph.tools.external_api  # trigger registration
from src.domain.ports.http_client_port import HttpClientPort
from src.main_graph.tools.external_api import clear_cache
from src.main_graph.tools.registry import TOOL_REGISTRY


def _config_with_http(http: AsyncMock) -> dict:
    return {"configurable": {"http": http}}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.mark.asyncio
async def test_osv_lookup_returns_vulnerabilities():
    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.post.return_value = {"vulns": [{"id": "GHSA-xxxx-yyyy-zzzz"}]}

    result = await TOOL_REGISTRY["osv_lookup"](
        package_name="lodash", version="4.17.15", config=_config_with_http(fake_http)
    )

    assert result["count"] == 1
    assert result["vulnerabilities"][0]["id"] == "GHSA-xxxx-yyyy-zzzz"
    fake_http.post.assert_awaited_once()
    call_kwargs = fake_http.post.await_args.kwargs
    assert call_kwargs["json"]["package"] == {"name": "lodash", "ecosystem": "npm"}
    assert call_kwargs["json"]["version"] == "4.17.15"


@pytest.mark.asyncio
async def test_osv_lookup_handles_http_error():
    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.post.side_effect = Exception("service unavailable")

    result = await TOOL_REGISTRY["osv_lookup"](
        package_name="lodash", config=_config_with_http(fake_http)
    )

    assert "error" in result
    assert result["vulnerabilities"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_osv_lookup.py -v`
Expected: FAIL — `TypeError: osv_lookup() got an unexpected keyword argument 'config'`

- [ ] **Step 3: Migrate `osv_lookup`**

Replace (currently `src/main_graph/tools/external_api.py:99-117`):

```python
@register("osv_lookup", "Queries OSV.dev for vulnerability records for a package version")
async def osv_lookup(package_name: str, config: RunnableConfig, version: str = "", ecosystem: str = "npm") -> dict:
    key = f"osv:{ecosystem}:{package_name}:{version}"
    if key in _cache:
        return _cache[key]
    payload = {"package": {"name": package_name, "ecosystem": ecosystem}}
    if version:
        payload["version"] = version
    http = get_services(config)["http"]
    try:
        data = await http.post("https://api.osv.dev/v1/query", json=payload)
        vulns = data.get("vulns", [])
        result = {"package": package_name, "version": version, "vulnerabilities": vulns, "count": len(vulns)}
        _cache[key] = result
        return result
    except Exception as exc:
        return {"error": str(exc), "vulnerabilities": []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_osv_lookup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/tools/external_api.py tests/unit/tools/test_osv_lookup.py
git commit -m "refactor: migrate osv_lookup to HttpClientPort"
```

---

### Task 6: Migrate `_get`/`_npm_metadata`/`_npm_weekly_downloads` and their callers (`package_reputation`, `unmaintained_packages`, `high_risk_packages`)

**Files:**
- Modify: `src/main_graph/tools/external_api.py:25-58` (`_get`, `_npm_metadata`, `_npm_weekly_downloads`), `:120-143` (`package_reputation`), `:146-164` (`unmaintained_packages`), `:204-237` (`high_risk_packages`)
- Test: `tests/unit/tools/test_package_reputation.py` (new)

**Interfaces:**
- Consumes: `get_services`, `RunnableConfig` (already imported).
- Produces: `_get(url, config, headers=None, params=None)`, `_npm_metadata(package_name, config)`, `_npm_weekly_downloads(package_name, config)` — all gain a required `config: RunnableConfig` parameter, same return values as before.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/tools/test_package_reputation.py`:

```python
from unittest.mock import AsyncMock

import pytest

import src.main_graph.tools.external_api  # trigger registration
from src.domain.ports.http_client_port import HttpClientPort
from src.main_graph.tools.external_api import clear_cache
from src.main_graph.tools.registry import TOOL_REGISTRY


def _config_with_http(http: AsyncMock) -> dict:
    return {"configurable": {"http": http}}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_cache()
    yield
    clear_cache()


_NPM_META = {
    "time": {"created": "2020-01-01T00:00:00.000Z", "modified": "2024-01-01T00:00:00.000Z"},
    "maintainers": [{"name": "alice"}],
    "dist-tags": {"latest": "1.2.3"},
}


@pytest.mark.asyncio
async def test_package_reputation_uses_http_port():
    async def _fake_get(url, headers=None, params=None):
        # _npm_metadata and _npm_weekly_downloads run concurrently via
        # asyncio.gather, so responses must be keyed by URL, not call order.
        if "downloads" in url:
            return {"downloads": 1000}
        return _NPM_META

    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.get.side_effect = _fake_get

    result = await TOOL_REGISTRY["package_reputation"](
        package_name="lodash", config=_config_with_http(fake_http)
    )

    assert result["latest_version"] == "1.2.3"
    assert result["weekly_downloads"] == 1000
    assert fake_http.get.await_count == 2


@pytest.mark.asyncio
async def test_unmaintained_packages_threads_config_through_metadata_calls(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"lodash": "^4.17.21"}}')

    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.get.return_value = {
        "time": {"created": "2020-01-01T00:00:00.000Z", "modified": "2020-02-01T00:00:00.000Z"},
        "maintainers": [{"name": "alice"}],
        "dist-tags": {"latest": "1.0.0"},
    }

    result = await TOOL_REGISTRY["unmaintained_packages"](
        repo_path=str(tmp_path), config=_config_with_http(fake_http)
    )

    assert result["checked"] == 1
    assert result["unmaintained"][0]["package"] == "lodash"
    fake_http.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_high_risk_packages_threads_config_through_metadata_calls(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"lodash": "^4.17.21"}}')

    fake_http = AsyncMock(spec=HttpClientPort)
    fake_http.get.return_value = dict(_NPM_META)

    result = await TOOL_REGISTRY["high_risk_packages"](
        repo_path=str(tmp_path), config=_config_with_http(fake_http)
    )

    assert result["checked"] == 1
    fake_http.get.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_package_reputation.py -v`
Expected: FAIL — `TypeError: package_reputation() got an unexpected keyword argument 'config'` (and similarly for the other two)

- [ ] **Step 3: Migrate the helpers**

Replace `_get`, `_npm_metadata`, `_npm_weekly_downloads` (currently `src/main_graph/tools/external_api.py:25-58`):

```python
async def _get(url: str, config: RunnableConfig, headers: dict | None = None, params: dict | None = None) -> dict:
    http = get_services(config)["http"]
    return await http.get(url, headers=headers, params=params)


async def _npm_metadata(package_name: str, config: RunnableConfig) -> dict:
    key = f"npm:{package_name}"
    if key in _cache:
        return _cache[key]
    try:
        data = await asyncio.wait_for(_get(f"https://registry.npmjs.org/{package_name}", config), _TIMEOUT)
        _cache[key] = data
        return data
    except Exception as exc:
        return {"error": str(exc)}


async def _npm_weekly_downloads(package_name: str, config: RunnableConfig) -> int | None:
    key = f"npm_dl:{package_name}"
    if key in _cache:
        return _cache[key]
    try:
        encoded = package_name.replace("/", "%2F")
        data = await asyncio.wait_for(
            _get(f"https://api.npmjs.org/downloads/point/last-week/{encoded}", config),
            _TIMEOUT,
        )
        count = data.get("downloads")
        _cache[key] = count
        return count
    except Exception:
        return None
```

- [ ] **Step 4: Migrate `package_reputation`**

Replace (currently `src/main_graph/tools/external_api.py:120-143`):

```python
@register("package_reputation", "Reports package age, maintainers, release cadence, popularity, and weekly downloads via npm registry")
async def package_reputation(package_name: str, config: RunnableConfig) -> dict:
    meta, weekly_downloads = await asyncio.gather(
        _npm_metadata(package_name, config),
        _npm_weekly_downloads(package_name, config),
    )
    if "error" in meta:
        return meta
    time_data = meta.get("time", {})
    versions = list(time_data.keys())
    created = time_data.get("created", "")
    modified = time_data.get("modified", "")
    maintainers = meta.get("maintainers", [])
    latest_ver = meta.get("dist-tags", {}).get("latest", "")
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
```

- [ ] **Step 5: Migrate `unmaintained_packages`**

Replace (currently `src/main_graph/tools/external_api.py:146-164`), changing only the signature and the `_npm_metadata` call:

```python
@register("unmaintained_packages", "Flags packages with no releases for 12+ months based on npm registry data")
async def unmaintained_packages(repo_path: str, config: RunnableConfig) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    cutoff = datetime.now(UTC) - timedelta(days=365)
    flagged = []
    deps_to_check = deps[:30]  # limit to avoid rate limiting
    metas = await asyncio.gather(*[_npm_metadata(d, config) for d in deps_to_check])
    for dep, meta in zip(deps_to_check, metas):
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
```

- [ ] **Step 6: Migrate `high_risk_packages`**

Replace (currently `src/main_graph/tools/external_api.py:204-237`), changing only the signature and the `_npm_metadata` call:

```python
@register("high_risk_packages", "Flags packages with unusual risk characteristics (new, single-maintainer, abandoned)")
async def high_risk_packages(repo_path: str, config: RunnableConfig) -> dict:
    pkg = _load_pkg(repo_path)
    deps = list(_all_deps(pkg).keys())
    cutoff_new = datetime.now(UTC) - timedelta(days=90)
    cutoff_abandoned = datetime.now(UTC) - timedelta(days=730)
    flagged = []
    deps_to_check = deps[:30]
    metas = await asyncio.gather(*[_npm_metadata(d, config) for d in deps_to_check])
    for dep, meta in zip(deps_to_check, metas):
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

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_package_reputation.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/main_graph/tools/external_api.py tests/unit/tools/test_package_reputation.py
git commit -m "refactor: migrate package_reputation, unmaintained_packages, and high_risk_packages to HttpClientPort"
```

---

### Task 7: Architecture boundary test + full regression run

**Files:**
- Modify: `tests/architecture/test_boundaries.py`

**Interfaces:**
- Consumes: nothing new — this is a static-analysis test over the finished `main_graph/tools/*.py` files from Tasks 3–6.
- Produces: `test_tools_do_not_import_httpx_directly()` — a permanent guard preventing regression back to direct `httpx` usage in tool files.

- [ ] **Step 1: Write the failing test**

Add to `tests/architecture/test_boundaries.py`:

```python
_FORBIDDEN_IN_TOOLS = {"httpx"}


def _tool_files():
    return [f for f in _SRC.glob("main_graph/tools/*.py") if f.name != "__init__.py"]


def test_tools_do_not_import_httpx_directly():
    """main_graph/tools/*.py must go through HttpClientPort, not import httpx directly."""
    violations = []
    for tool_file in _tool_files():
        imports = _get_imports(tool_file)
        bad = imports & _FORBIDDEN_IN_TOOLS
        if bad:
            violations.append(f"{tool_file.relative_to(_SRC)}: {bad}")
    assert not violations, "Forbidden imports in tool files:\n" + "\n".join(violations)
```

This should already pass at this point, since Tasks 3–6 removed the `httpx` import from `external_api.py` and no other file in `main_graph/tools/` ever imported it. It's written as a guard against regression, not to drive new implementation.

- [ ] **Step 2: Run the new test to confirm it passes**

Run: `cd apps/backend && uv run pytest tests/architecture/test_boundaries.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 3: Run the full unit test suite**

Run: `cd apps/backend && uv run pytest tests/unit tests/architecture -v`
Expected: PASS — no regressions in `tests/unit/nodes/`, `tests/unit/services/`, `tests/unit/tools/`, or `tests/architecture/`.

- [ ] **Step 4: Commit**

```bash
git add tests/architecture/test_boundaries.py
git commit -m "test: guard main_graph/tools against direct httpx imports"
```
