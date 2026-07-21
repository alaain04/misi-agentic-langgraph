# A3 — Input Caching by Commit SHA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache the deterministic, expensive discovery inputs keyed by commit SHA so re-runs of the same repo+commit (the determinism/fixture-corpus testing workflow) skip recomputation — chiefly the `npm audit` container call.

**Architecture:** Capture the resolved commit SHA at clone time and carry it (with `repo_url`) onto `PrepResult`. A generic Mongo-backed `InputCacheDAO` (get/put keyed by `(repo_url, sha, pm, kind)`, optional age check) is injected via the pipeline `configurable`, alongside pure helpers (`cache_key`, `is_fresh`, `get_or_compute`). Two consumers use it: `save_prep_result` caches the dependency graph (indefinite), and `VulnerabilityAgent` caches the `npm audit` output (short TTL, since advisories publish over time). The cache is always fallback-safe: a miss or error recomputes.

**Tech Stack:** Python 3.12, pymongo (async), Pydantic v2, pytest, ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-07-20-reliability-a3-input-caching-design.md`

## Global Constraints

- Package manager: `uv` (`uv run <cmd>`), never pip/bare python.
- No emoji in code, comments, or commit messages.
- Backend only.
- The cache is an optimization, **never a correctness dependency**: on cache miss OR any cache error, always fall back to recompute.
- Cache hits only occur on re-runs of the identical `(repo_url, commit_sha, package_manager)`. Normal (distinct-repo) jobs never hit it — that is expected.
- Pure helpers (`cache_key`, `is_fresh`, `get_or_compute`) are unit-tested without a DB; the thin DAO Mongo methods follow the existing `ResultDAO` untested-thin-wrapper convention.
- Before claiming done: run `uv run pytest`, `uv run ruff check .`, `uv run mypy src` from `apps/backend`.
- All commands below run from `apps/backend/`.

---

### Task 1: Capture commit SHA and repo_url onto PrepResult

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/nodes/clone_repo.py`
- Modify: `src/main_graph/subgraphs/discovery/state.py`
- Modify: `src/models/results.py` (`PrepResult`)
- Modify: `src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`
- Test: `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py` (append), `tests/unit/test_result_models.py` (append)

**Interfaces:**
- `clone_repo` returns `commit_sha: str` in its state dict (empty string if rev-parse fails).
- `DiscoveryState` gains `commit_sha: NotRequired[str]`.
- `PrepResult` gains `commit_sha: str = ""` and `repo_url: str = ""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/subgraphs/discovery/test_discovery_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_clone_repo_captures_commit_sha(tmp_path):
    container = AsyncMock()
    # first call: git clone (success); second call: git rev-parse HEAD
    container.run.side_effect = [(0, "", ""), (0, "abc123def\n", "")]

    state = {**_BASE_STATE, "job_id": "sha-job"}
    result = await clone_repo(state, _config(container=container))

    assert result.get("commit_sha") == "abc123def"
    assert "discovery_error" not in result


@pytest.mark.asyncio
async def test_clone_repo_sha_empty_when_rev_parse_fails(tmp_path):
    container = AsyncMock()
    container.run.side_effect = [(0, "", ""), (1, "", "fatal")]

    state = {**_BASE_STATE, "job_id": "sha-job2"}
    result = await clone_repo(state, _config(container=container))

    # clone succeeded, so no discovery_error; sha just unavailable
    assert result.get("commit_sha") == ""
    assert "discovery_error" not in result
```

Append to `tests/unit/test_result_models.py`:

```python
def test_prep_result_commit_sha_and_repo_url_default_empty():
    r = PrepResult(
        job_id="j1", repo_path="/tmp/r", project_metadata={}, manifest_files=[],
        detected_package_manager="npm", dependency_graph={"direct": {}},
        discovery_summary="s", vector_store_id="vs1",
    )
    assert r.commit_sha == ""
    assert r.repo_url == ""


def test_prep_result_accepts_commit_sha_and_repo_url():
    r = PrepResult(
        job_id="j1", repo_path="/tmp/r", project_metadata={}, manifest_files=[],
        detected_package_manager="npm", dependency_graph={"direct": {}},
        discovery_summary="s", vector_store_id="vs1",
        commit_sha="deadbeef", repo_url="https://github.com/x/y",
    )
    assert r.commit_sha == "deadbeef"
    assert r.repo_url == "https://github.com/x/y"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/discovery/test_discovery_orchestrator.py -k commit_sha tests/unit/test_result_models.py -k "commit_sha or repo_url" -v`
Expected: FAIL (clone_repo doesn't return commit_sha; PrepResult has no such fields).

- [ ] **Step 3: Implement**

In `src/main_graph/subgraphs/discovery/nodes/clone_repo.py`, after the clone-success `logger.info` and before `return {"repo_path": tmp_dir}`, resolve the SHA:

```python
    logger.info("clone_repo: success repo_url=%s", repo_url)

    sha_rc, sha_out, _sha_err = await container.run(
        image=_GIT_IMAGE,
        command="cd /workspace && git rev-parse HEAD",
        volume=f"{tmp_dir}:/workspace",
        run_as_root=True,
    )
    commit_sha = sha_out.strip() if sha_rc == 0 else ""
    return {"repo_path": tmp_dir, "commit_sha": commit_sha}
```

In `src/main_graph/subgraphs/discovery/state.py`, add to `DiscoveryState` (near `repo_path`):

```python
    commit_sha: NotRequired[str]
```

In `src/models/results.py`, add to `PrepResult` (after `repo_path`):

```python
    repo_url: str = ""
    commit_sha: str = ""
```

In `src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`, add the two fields to the `PrepResult(...)` construction:

```python
        repo_url=state.get("repo_url", ""),
        commit_sha=state.get("commit_sha") or "",
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/discovery/test_discovery_orchestrator.py tests/unit/test_result_models.py -v`
Expected: PASS (including the 4 new tests and all pre-existing).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/main_graph/subgraphs/discovery/nodes/clone_repo.py src/main_graph/subgraphs/discovery/state.py src/models/results.py src/main_graph/subgraphs/discovery/nodes/save_prep_result.py tests/unit/subgraphs/discovery/test_discovery_orchestrator.py tests/unit/test_result_models.py && uv run mypy src/models/results.py src/main_graph/subgraphs/discovery/nodes/clone_repo.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/clone_repo.py src/main_graph/subgraphs/discovery/state.py src/models/results.py src/main_graph/subgraphs/discovery/nodes/save_prep_result.py tests/unit/subgraphs/discovery/test_discovery_orchestrator.py tests/unit/test_result_models.py
git commit -m "feat: capture commit SHA and repo_url onto PrepResult"
```

---

### Task 2: Input cache infrastructure

**Files:**
- Create: `src/db/input_cache.py`
- Modify: `src/services/dependencies.py`
- Modify: `src/main_graph/config.py`
- Modify: `src/services/job_runner.py`
- Test: `tests/unit/db/test_input_cache.py` (create; add `tests/unit/db/__init__.py` if the package dir does not exist)

**Interfaces:**
- `cache_key(repo_url: str, commit_sha: str, pm: str, kind: str) -> str` (pure)
- `is_fresh(created_at_iso: str, max_age_seconds: float, now: datetime) -> bool` (pure)
- `get_or_compute(cache, key, compute, max_age_seconds=None) -> dict` — async; `cache` has async `get(key, max_age_seconds) -> dict | None` and `put(key, data) -> None`; `compute` is an async no-arg callable returning a dict.
- `InputCacheDAO` — Mongo-backed implementation of `get`/`put`.
- `get_input_cache() -> InputCacheDAO`.
- `PipelineConfigurable` gains `input_cache: InputCacheDAO`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/db/__init__.py` (empty) if missing, then `tests/unit/db/test_input_cache.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.db.input_cache import cache_key, get_or_compute, is_fresh


def test_cache_key_is_stable_and_distinct():
    k1 = cache_key("https://x/y", "sha1", "npm", "npm_audit")
    k2 = cache_key("https://x/y", "sha1", "npm", "npm_audit")
    k3 = cache_key("https://x/y", "sha1", "npm", "dependency_graph")
    assert k1 == k2
    assert k1 != k3  # kind differentiates


def test_is_fresh_within_age():
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    created = (now - timedelta(hours=1)).isoformat()
    assert is_fresh(created, max_age_seconds=7200, now=now) is True


def test_is_fresh_expired():
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    created = (now - timedelta(hours=3)).isoformat()
    assert is_fresh(created, max_age_seconds=7200, now=now) is False


def test_is_fresh_unparseable_is_not_fresh():
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    assert is_fresh("not-a-date", max_age_seconds=7200, now=now) is False


class _FakeCache:
    def __init__(self, initial: dict | None = None):
        self.store = dict(initial or {})
        self.put_calls: list[str] = []

    async def get(self, key, max_age_seconds=None):
        return self.store.get(key)

    async def put(self, key, data):
        self.put_calls.append(key)
        self.store[key] = data


@pytest.mark.asyncio
async def test_get_or_compute_hit_skips_compute():
    cache = _FakeCache({"k": {"cached": True}})
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return {"cached": False}

    result = await get_or_compute(cache, "k", compute)
    assert result == {"cached": True}
    assert calls["n"] == 0
    assert cache.put_calls == []


@pytest.mark.asyncio
async def test_get_or_compute_miss_computes_and_puts():
    cache = _FakeCache()
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return {"fresh": True}

    result = await get_or_compute(cache, "k", compute)
    assert result == {"fresh": True}
    assert calls["n"] == 1
    assert cache.put_calls == ["k"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/db/test_input_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db.input_cache'`.

- [ ] **Step 3: Implement the cache module**

Create `src/db/input_cache.py`:

```python
"""Commit-SHA-keyed cache for deterministic, expensive pipeline inputs.

A cache HIT only ever happens when the exact same (repo_url, commit_sha,
package_manager, kind) is analyzed again — i.e. re-runs of the same commit
(the determinism/fixture-corpus testing workflow). The cache is strictly an
optimization: callers must fall back to recompute on miss or error.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from src.db.connection import get_db

logger = logging.getLogger(__name__)


def cache_key(repo_url: str, commit_sha: str, pm: str, kind: str) -> str:
    return f"{repo_url}@{commit_sha}:{pm}:{kind}"


def is_fresh(created_at_iso: str, max_age_seconds: float, now: datetime) -> bool:
    """True if the entry's age is within max_age_seconds. Unparseable
    timestamps are treated as not fresh (force recompute)."""
    try:
        created = datetime.fromisoformat(created_at_iso)
    except (ValueError, TypeError):
        return False
    return (now - created).total_seconds() <= max_age_seconds


async def get_or_compute(
    cache: InputCacheDAO,
    key: str,
    compute: Callable[[], Awaitable[dict]],
    max_age_seconds: float | None = None,
) -> dict:
    """Return the cached value for key, else compute it, store it, and return.

    Never raises on cache failure — a cache error degrades to a plain compute.
    """
    try:
        cached = await cache.get(key, max_age_seconds)
    except Exception as exc:
        logger.warning("input_cache: get failed for %s: %s", key, exc)
        cached = None
    if cached is not None:
        return cached

    value = await compute()

    try:
        await cache.put(key, value)
    except Exception as exc:
        logger.warning("input_cache: put failed for %s: %s", key, exc)
    return value


class InputCacheDAO:
    def __init__(self) -> None:
        self._col = get_db()["input_cache"]

    async def get(self, key: str, max_age_seconds: float | None = None) -> dict | None:
        doc = await self._col.find_one({"key": key}, {"_id": 0})
        if doc is None:
            return None
        if max_age_seconds is not None and not is_fresh(
            doc.get("created_at", ""), max_age_seconds, datetime.now(UTC)
        ):
            return None
        data = doc.get("data")
        return data if isinstance(data, dict) else None

    async def put(self, key: str, data: dict) -> None:
        await self._col.update_one(
            {"key": key},
            {"$set": {"key": key, "data": data, "created_at": datetime.now(UTC).isoformat()}},
            upsert=True,
        )
```

In `src/services/dependencies.py`, add (mirroring `get_result_dao`):

```python
from src.db.input_cache import InputCacheDAO


def get_input_cache() -> InputCacheDAO:
    return InputCacheDAO()
```

In `src/main_graph/config.py`, add the import and the field:

```python
from src.db.input_cache import InputCacheDAO
```

and in `PipelineConfigurable`:

```python
    input_cache: InputCacheDAO
```

In `src/services/job_runner.py` `_build_config`, add to the `configurable` dict (and import `get_input_cache` from `src.services.dependencies` alongside `get_result_dao`):

```python
            "result_dao": get_result_dao(),
            "input_cache": get_input_cache(),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/db/test_input_cache.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/db/input_cache.py src/services/dependencies.py src/main_graph/config.py src/services/job_runner.py tests/unit/db/test_input_cache.py && uv run mypy src/db/input_cache.py src/main_graph/config.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/db/input_cache.py src/services/dependencies.py src/main_graph/config.py src/services/job_runner.py tests/unit/db/
git commit -m "feat: add commit-SHA-keyed input cache infrastructure"
```

---

### Task 3: Cache the dependency graph in save_prep_result

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`

**Interfaces:**
- Consumes `cache_key`, `get_or_compute` (Task 2) and the injected `input_cache` service. Caches the dependency graph indefinitely (no TTL — it is a pure function of the committed source).

- [ ] **Step 1: Implement**

In `src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`, add imports:

```python
from src.db.input_cache import cache_key, get_or_compute
```

Replace the `dependency_graph=build_dependency_graph(state.get("repo_path", ""), pm),` line in the `PrepResult(...)` construction with a pre-computed `dep_graph`. Before the `result = PrepResult(...)` block, add:

```python
    svc = get_services(config)
    dao = svc["result_dao"]
    repo_path = state.get("repo_path", "")
    repo_url = state.get("repo_url", "")
    commit_sha = state.get("commit_sha") or ""
    cache = svc.get("input_cache")

    async def _build() -> dict:
        return build_dependency_graph(repo_path, pm)

    if cache is not None and commit_sha:
        dep_graph = await get_or_compute(
            cache, cache_key(repo_url, commit_sha, pm, "dependency_graph"), _build
        )
    else:
        dep_graph = await _build()
```

(Remove the now-duplicate `dao = get_services(config)["result_dao"]` line and `pm = ...` if it now appears twice — keep a single `pm` assignment and a single `dao`.) Then in the `PrepResult(...)` set:

```python
        dependency_graph=dep_graph,
```

- [ ] **Step 2: Run the discovery tests**

Run: `uv run pytest tests/unit/subgraphs/discovery/ tests/subgraphs/test_discovery_subgraph.py -v`
Expected: PASS (no regression; the integration test exercises `save_prep_result` end to end with a real cache service present or absent).

- [ ] **Step 3: Lint and type-check**

Run: `uv run ruff check src/main_graph/subgraphs/discovery/nodes/save_prep_result.py && uv run mypy src/main_graph/subgraphs/discovery/nodes/save_prep_result.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/main_graph/subgraphs/discovery/nodes/save_prep_result.py
git commit -m "feat: cache dependency graph by commit SHA in save_prep_result"
```

---

### Task 4: Cache npm audit in VulnerabilityAgent

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/agents/base_agent.py` (`run` signature)
- Modify: `src/main_graph/subgraphs/analysis/agents/maintenance_agent.py` (`run` signature)
- Modify: `src/main_graph/subgraphs/analysis/agents/license_agent.py` (`run` signature)
- Modify: `src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py` (`run` uses the cache)
- Modify: `src/main_graph/subgraphs/analysis/nodes/domain_agent.py` (pass the cache)
- Test: `tests/unit/test_base_agent.py` or a new `tests/unit/test_vulnerability_agent.py` (create)

**Interfaces:**
- All four `run` methods accept an optional `cache: InputCacheDAO | None = None` (only `VulnerabilityAgent` uses it; the others accept and ignore it, keeping a uniform call site).
- `domain_agent` passes `cache=svc.get("input_cache")`.
- The npm audit output is cached with a short TTL (`_AUDIT_TTL_SECONDS`) because advisories publish over time.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_vulnerability_agent.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import (
    VulnerabilityAgent,
)
from src.models.results import AgentDispatch, PrepResult


def _prep(**kw) -> PrepResult:
    defaults = dict(
        job_id="j1", repo_path="/tmp/r", project_metadata={}, manifest_files=[],
        detected_package_manager="npm", dependency_graph={"direct": {}},
        discovery_summary="s", vector_store_id="",
        repo_url="https://github.com/x/y", commit_sha="sha1",
    )
    return PrepResult(**{**defaults, **kw})


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="security", hypothesis="h", packages_to_focus=[],
        agent_type="vulnerability_agent",
    )


class _FakeCache:
    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.put_calls = []

    async def get(self, key, max_age_seconds=None):
        return self.store.get(key)

    async def put(self, key, data):
        self.put_calls.append(key)
        self.store[key] = data


@pytest.mark.asyncio
async def test_vulnerability_agent_uses_cached_audit():
    # cache pre-populated -> npm_audit must NOT run
    from src.main_graph.subgraphs.analysis.agents import vulnerability_agent as va
    from src.db.input_cache import cache_key

    prep = _prep()
    key = cache_key(prep.repo_url, prep.commit_sha, "npm", "npm_audit")
    cache = _FakeCache({key: {"vulnerabilities": {}}})
    audit_mock = AsyncMock()

    with patch.object(va, "npm_audit", audit_mock):
        bundle, tools, _ = await VulnerabilityAgent().run(
            _dispatch(), prep, container=AsyncMock(), cache=cache
        )

    audit_mock.assert_not_awaited()
    assert cache.put_calls == []


@pytest.mark.asyncio
async def test_vulnerability_agent_populates_cache_on_miss():
    from src.main_graph.subgraphs.analysis.agents import vulnerability_agent as va

    prep = _prep()
    cache = _FakeCache()
    audit_mock = AsyncMock(return_value={"vulnerabilities": {}})

    with patch.object(va, "npm_audit", audit_mock):
        await VulnerabilityAgent().run(
            _dispatch(), prep, container=AsyncMock(), cache=cache
        )

    audit_mock.assert_awaited_once()
    assert len(cache.put_calls) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_vulnerability_agent.py -v`
Expected: FAIL — `run()` does not accept a `cache` kwarg yet.

- [ ] **Step 3: Implement**

Add `cache: InputCacheDAO | None = None` to the `run` signature of each agent. For **base_agent.py**, **maintenance_agent.py**, and **license_agent.py**, add the parameter and a matching import (`from src.db.input_cache import InputCacheDAO`); these ignore it (base already delegates to `_react_loop` without it; maintenance still calls `super().run(dispatch, prep, container)`; license is unchanged internally).

For **vulnerability_agent.py**, add the import and constant, and wrap the audit call:

```python
from src.db.input_cache import InputCacheDAO, cache_key, get_or_compute

_AUDIT_TTL_SECONDS = 7 * 24 * 3600  # advisories publish over time; re-audit weekly
```

Change `run` to accept `cache` and route the audit through it (replacing the direct `output = await npm_audit(...)`):

```python
    async def run(
        self,
        dispatch: AgentDispatch,
        prep: PrepResult,
        container: ContainerRunPort | None = None,
        cache: InputCacheDAO | None = None,
    ) -> tuple[EvidenceBundle, list[str], int]:
        async def _audit() -> dict:
            return await npm_audit(
                repo_path=prep.repo_path,
                container=container,
                docker_image=prep.docker_image,
                detected_package_manager=prep.detected_package_manager,
            )

        if cache is not None and prep.commit_sha:
            key = cache_key(
                prep.repo_url, prep.commit_sha, prep.detected_package_manager, "npm_audit"
            )
            output = await get_or_compute(cache, key, _audit, _AUDIT_TTL_SECONDS)
        else:
            output = await _audit()
        # ... the rest of the existing method (min_severity, parse, bundle) unchanged,
        #     operating on `output`
```

In **domain_agent.py**, pass the cache when running the agent:

```python
    bundle, tools_used, react_iterations = await agent.run(
        dispatch, prep, container, cache=svc.get("input_cache")
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_vulnerability_agent.py tests/unit/test_base_agent.py -v`
Expected: PASS (new agent tests + no regression in base-agent tests).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/main_graph/subgraphs/analysis/agents/ src/main_graph/subgraphs/analysis/nodes/domain_agent.py tests/unit/test_vulnerability_agent.py && uv run mypy src/main_graph/subgraphs/analysis`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/analysis/agents/ src/main_graph/subgraphs/analysis/nodes/domain_agent.py tests/unit/test_vulnerability_agent.py
git commit -m "feat: cache npm audit output by commit SHA in VulnerabilityAgent"
```

---

### Task 5: Verification

**Files:** none.

- [ ] **Step 1: Full suite**

Run: `uv run pytest`
Expected: all pass.

- [ ] **Step 2: Lint + type-check the backend**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 3: Optional live confirmation**

With a backend + MongoDB + Docker running (started from THIS checkout — a stale backend would not have the cache code), run the same repo twice and confirm the second run's `npm_audit` is served from cache (check backend logs for the absence of a second `npm audit` container run, and that the `input_cache` collection has an `npm_audit` entry). The unit tests are the real gate.

- [ ] **Step 4: Commit any residual fixes**

```bash
git add -A
git commit -m "test: verify input caching across backend suite"
```

---

## Self-Review Notes

- **Spec coverage:** SHA capture → Task 1; cache infra (Mongo DAO + key + TTL + fallback) → Task 2; dependency-graph cache (indefinite) → Task 3; npm audit cache (TTL) → Task 4. Codegraph-index and node_modules caching are explicitly out of scope (on-disk working state, too invasive) per the refined 2026-07-20 scope decision.
- **Fallback-safe:** `get_or_compute` swallows cache get/put errors and recomputes; consumers also guard on `cache is not None and commit_sha`.
- **Honest value note:** the dependency-graph cache (Task 3) is low-value (building the graph is a fast, already-deterministic lockfile parse) — included for completeness per the scope decision; the real win is the npm-audit cache (Task 4), a container call.
- **Type consistency:** `cache_key`/`is_fresh`/`get_or_compute` signatures are used identically in Tasks 2-4; the `cache` kwarg is added uniformly to all four `run` methods so `domain_agent`'s single call site type-checks.
