    # Hexagonal HTTP Port — external_api.py

**Date:** 2026-07-15
**Scope:** `apps/backend/src/main_graph/tools/external_api.py`

---

## Problem

`ContainerRunPort` and `JobRepositoryPort` are correctly abstracted and injected via `PipelineConfigurable`. `main_graph/tools/external_api.py` is not: every tool function (`github_advisory`, `osv_lookup`, `web_search`, plus the `_npm_metadata`/`_npm_weekly_downloads`/`_get` helpers backing `package_reputation`, `unmaintained_packages`, `high_risk_packages`) instantiates `httpx.AsyncClient()` directly, bypassing the port/adapter boundary entirely. There is no seam to swap the HTTP client or test these tools without hitting real endpoints (npm registry, GitHub GraphQL, OSV.dev, Tavily).

## Non-goals

- **LLM calls** (`conductor.py`, `report_builder.py`, `subgraphs/discovery/nodes/build_dependency_summary.py`, all via `utils/llm.get_llm()`) — the 2026-05-22 hexagonal-adequation design explicitly decided the LLM is "the core execution engine of the pipeline, not peripheral infrastructure" and should not be ported. That decision stands and is reaffirmed here; these 3 call sites are untouched.
- **`main_graph/tools/npm_cli.py`** — shells out to `npm` on the host via `asyncio.create_subprocess_exec`. Routing it through `ContainerRunPort` would change runtime behavior (every npm call would run in a fresh container instead of on the host), which is a separate decision from this port-boundary fix. Deferred.
- **`services/job_dao.py` location** (implements `JobRepositoryPort` but lives outside `main_graph/adapters/`) and **`utils/workers_client.py`** (dead code, zero import sites) — unrelated to this change, left as-is.

---

## Approach: generic `HttpClientPort`, matching the `ContainerRunPort` precedent

One port with `get`/`post`, not one port per external API (GitHub, OSV, Tavily, npm registry). `ContainerRunPort` is already a generic "run a container" capability rather than e.g. `RunNpmAuditPort`; a generic HTTP port follows the same convention and avoids four near-identical port/adapter pairs for what is currently one file. Business logic (GraphQL query text, OSV payload shape, Tavily params, API-key lookup, session-level caching) stays exactly where it lives today, in `external_api.py` — only the raw `httpx.AsyncClient()` construction moves behind the port.

### `src/domain/ports/http_client_port.py` (new)

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

### `src/main_graph/adapters/httpx_client_adapter.py` (new)

`HttpxClientAdapter(HttpClientPort)` — wraps `httpx.AsyncClient(timeout=10.0)`, same timeout as today's `_TIMEOUT`. `get`/`post` call `raise_for_status()` then return `r.json()`, matching today's `_get()` helper behavior exactly. No new exception types — `httpx` exceptions propagate as they do today, since every call site already catches broad `Exception` and returns `{"error": str(exc)}`.

---

## Injection mechanism

`external_api.py` functions are `@register`-based tools, not LangChain `@tool`s — they're invoked by `tool_runner.py::_run_tool` via signature introspection, not passed `config` today:

```python
# tool_runner.py, current (src/main_graph/nodes/tool_runner.py:29-33)
sig = inspect.signature(fn)
kwargs = dict(tc.args)
if "repo_path" in sig.parameters:
    kwargs["repo_path"] = repo_path
output = await fn(**kwargs)
```

Extend the same pattern with one more line, and thread `config` down from the outer node (which already receives it):

```python
async def _run_tool(tc: ToolCall, repo_path: str, config: RunnableConfig) -> ToolResult:
    ...
    if "repo_path" in sig.parameters:
        kwargs["repo_path"] = repo_path
    if "config" in sig.parameters:
        kwargs["config"] = config
    output = await fn(**kwargs)
```

`tool_runner(state, config)` passes `config` into `_run_tool` at its call site. Tool functions that don't need HTTP (`package_files.py`, `registry.py`, `npm_cli.py`) are untouched — they simply don't declare a `config` parameter, so nothing is injected.

---

## `external_api.py` changes

Every function that talks to the network gains a `config: RunnableConfig` parameter and reads `get_services(config)["http"]` instead of constructing `httpx.AsyncClient()`:

| Function | Change |
|---|---|
| `_get(url, headers, params)` | becomes `_get(url, config, headers=None, params=None)`, delegates to `get_services(config)["http"].get(...)` |
| `_npm_metadata(package_name)` | gains `config` param, passes through to `_get` |
| `_npm_weekly_downloads(package_name)` | gains `config` param, passes through to `_get` |
| `github_advisory(package_name, ecosystem, config)` | replaces inline `httpx.AsyncClient().post(...)` with `get_services(config)["http"].post(...)` |
| `osv_lookup(package_name, version, ecosystem, config)` | same |
| `web_search(query, config)` | same |
| `package_reputation(package_name, config)` | gains `config`, passes to `_npm_metadata`/`_npm_weekly_downloads` |
| `unmaintained_packages(repo_path, config)` | gains `config`, passes to `_npm_metadata` |
| `high_risk_packages(repo_path, config)` | gains `config`, passes to `_npm_metadata` |

`typosquat_detection` and `resolve_transitive_parent`'s local-file logic are untouched — they make no HTTP calls. Caching (`_cache` dict, `clear_cache()`) is untouched.

---

## Wiring

- `src/main_graph/config.py` — add `http: HttpClientPort` to `PipelineConfigurable`.
- `src/services/job_runner.py::_build_config` — instantiate `HttpxClientAdapter()` once per job, add to the `configurable` dict alongside `container`.

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

---

## Testing

`external_api.py` functions become mockable with a fake `HttpClientPort` (e.g. `AsyncMock(spec=HttpClientPort)`) — no real network calls needed in unit tests. Implementation follows the project's TDD process: tests are added/adjusted alongside each migrated function.

---

## File change summary

**New files:**
- `src/domain/ports/http_client_port.py`
- `src/main_graph/adapters/httpx_client_adapter.py`

**Modified files:**
- `src/main_graph/config.py` — add `http: HttpClientPort`
- `src/services/job_runner.py` — instantiate and inject `HttpxClientAdapter`
- `src/main_graph/nodes/tool_runner.py` — thread `config` into `_run_tool`, extend signature-introspection injection
- `src/main_graph/tools/external_api.py` — 9 functions gain `config: RunnableConfig`, replace raw `httpx` with `get_services(config)["http"]`
