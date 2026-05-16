# Worker Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the npm-worker with typed exceptions, a Redis multi-window rate limiter, NATS NAK-based retries, a FetcherEntry registry, and three GitHub fetchers (issues, releases, advisories).

**Architecture:** Fetchers raise typed exceptions (`RateLimitError`, `TransientFetchError`, `PermanentFetchError`). The consumer dispatches ack/nak/term based on exception type. A Redis Lua-script rate limiter replaces the in-memory TokenBucket and enforces multiple time windows atomically. Three GitHub fetch functions share one rate group.

**Tech Stack:** Python 3.12, FastAPI, NATS JetStream (nats-py), Redis (redis-py ≥ 5 asyncio), MongoDB (motor/pymongo async), httpx, pydantic-settings v2, uv, pytest-asyncio.

**Run all tests with:** `cd worker && uv run pytest tests/unit/ -q`

---

### Task 1: Typed fetch exceptions

**Files:**
- Create: `src/fetchers/errors.py`
- Create: `tests/unit/test_fetch_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_fetch_errors.py
from src.fetchers.errors import RateLimitError, TransientFetchError, PermanentFetchError


def test_rate_limit_error_stores_delay():
    err = RateLimitError(delay=30.0)
    assert err.delay == 30.0
    assert isinstance(err, Exception)


def test_transient_fetch_error_is_exception():
    err = TransientFetchError("network timeout")
    assert str(err) == "network timeout"


def test_permanent_fetch_error_is_exception():
    err = PermanentFetchError("404 not found")
    assert str(err) == "404 not found"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd worker && uv run pytest tests/unit/test_fetch_errors.py -v
```
Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Write implementation**

```python
# src/fetchers/errors.py


class RateLimitError(Exception):
    """API responded 429. delay is seconds to wait before retrying."""

    def __init__(self, delay: float) -> None:
        super().__init__(f"rate limited, retry after {delay}s")
        self.delay = delay


class TransientFetchError(Exception):
    """Temporary failure (5xx, network error). Safe to retry."""


class PermanentFetchError(Exception):
    """Unrecoverable failure (404, parse error). Do not retry."""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd worker && uv run pytest tests/unit/test_fetch_errors.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add worker/src/fetchers/errors.py worker/tests/unit/test_fetch_errors.py
git commit -m "feat(worker): add typed fetch exceptions"
```

---

### Task 2: Update npm fetcher to raise typed exceptions

**Files:**
- Modify: `src/fetchers/npm.py`
- Modify: `tests/unit/test_npm_fetcher.py`

- [ ] **Step 1: Read the current test file**

```bash
cat worker/tests/unit/test_npm_fetcher.py
```

- [ ] **Step 2: Rewrite the test file**

```python
# tests/unit/test_npm_fetcher.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.fetchers.npm import fetch
from src.rate_limiter import RateLimiter


def _make_limiter() -> RateLimiter:
    limiter = MagicMock(spec=RateLimiter)
    limiter.acquire = AsyncMock()
    return limiter


def _resp(status: int, body: dict | None = None, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body or {})
    r.headers = headers or {}
    return r


@pytest.mark.asyncio
async def test_fetch_returns_doc_on_200():
    reg = _resp(200, {"name": "react"})
    dl = _resp(200, {"downloads": 1000})
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[reg, dl])
    limiter = _make_limiter()

    with patch("src.fetchers.npm.settings") as s:
        s.max_retries = 3
        doc = await fetch(client, "react", limiter, max_retries=3)

    assert doc["registry_data"] == {"name": "react"}
    assert doc["weekly_downloads"] == 1000


@pytest.mark.asyncio
async def test_fetch_raises_rate_limit_error_on_429():
    r = _resp(429, headers={"Retry-After": "60"})
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    limiter = _make_limiter()

    with pytest.raises(RateLimitError) as exc_info:
        await fetch(client, "react", limiter, max_retries=1)
    assert exc_info.value.delay == 60.0


@pytest.mark.asyncio
async def test_fetch_raises_permanent_error_on_404():
    r = _resp(404)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    limiter = _make_limiter()

    with pytest.raises(PermanentFetchError):
        await fetch(client, "no-such-package", limiter, max_retries=3)


@pytest.mark.asyncio
async def test_fetch_raises_transient_error_on_500():
    r = _resp(500)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    limiter = _make_limiter()

    with pytest.raises(TransientFetchError):
        await fetch(client, "react", limiter, max_retries=3)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd worker && uv run pytest tests/unit/test_npm_fetcher.py -v
```
Expected: failures due to wrong signatures / missing exceptions

- [ ] **Step 4: Rewrite `src/fetchers/npm.py`**

```python
"""Fetch npm registry metadata and weekly downloads."""

import asyncio
import logging
import urllib.parse

import httpx

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
_TIMEOUT = 15.0


def _encode(name: str) -> str:
    return urllib.parse.quote(name, safe="")


async def _get(
    client: httpx.AsyncClient,
    url: str,
    rate_limiter: RateLimiter,
    max_retries: int,
) -> httpx.Response:
    """Fetch one URL, acquiring a rate-limit slot first. Raises on non-200."""
    for attempt in range(max_retries):
        await rate_limiter.acquire("npm")
        try:
            resp = await client.get(url, timeout=_TIMEOUT)
        except httpx.RequestError as exc:
            if attempt == max_retries - 1:
                raise TransientFetchError(str(exc)) from exc
            await asyncio.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            return resp
        if resp.status_code == 429:
            delay = float(resp.headers.get("Retry-After", 60))
            raise RateLimitError(delay)
        if resp.status_code == 404:
            raise PermanentFetchError(f"404 for {url}")
        if resp.status_code >= 500:
            if attempt == max_retries - 1:
                raise TransientFetchError(f"status {resp.status_code} for {url}")
            await asyncio.sleep(2 ** attempt)
            continue
        raise PermanentFetchError(f"unexpected status {resp.status_code} for {url}")

    raise TransientFetchError(f"exhausted {max_retries} retries for {url}")


async def fetch(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Return cache document dict for one npm package."""
    encoded = _encode(name)
    reg_resp, dl_resp = await asyncio.gather(
        _get(client, f"{_NPM_REGISTRY}/{encoded}", rate_limiter, max_retries),
        _get(client, f"{_NPM_DOWNLOADS}/{encoded}", rate_limiter, max_retries),
    )
    return {
        "registry_data": reg_resp.json(),
        "weekly_downloads": dl_resp.json().get("downloads"),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd worker && uv run pytest tests/unit/test_npm_fetcher.py -v
```
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add worker/src/fetchers/npm.py worker/tests/unit/test_npm_fetcher.py
git commit -m "feat(worker): npm fetcher raises typed exceptions, accepts RateLimiter"
```

---

### Task 3: Redis client

**Files:**
- Create: `src/redis_client.py`

No unit test — this is infrastructure wiring (mirrors `nats_client.py` pattern already untested).

- [ ] **Step 1: Add redis dependency**

```bash
cd worker && uv add redis
```

- [ ] **Step 2: Create `src/redis_client.py`**

```python
# src/redis_client.py
import logging

import redis.asyncio as aioredis

from src.config import settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None


async def connect() -> None:
    global _client
    if _client is not None:
        return
    _client = aioredis.from_url(settings.redis_url, decode_responses=False)
    await _client.ping()
    logger.info("redis: connected to %s", settings.redis_url)


async def close() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


def get_client() -> aioredis.Redis:
    if _client is None:
        raise RuntimeError("Redis not connected — call connect() first")
    return _client
```

- [ ] **Step 3: Commit**

```bash
git add worker/src/redis_client.py
git commit -m "feat(worker): add async Redis client"
```

---

### Task 4: Redis multi-window rate limiter

**Files:**
- Modify: `src/rate_limiter.py`
- Create: `tests/unit/test_rate_limiter.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_rate_limiter.py
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.rate_limiter import RateLimiter


def _make_limiter(windows: dict) -> RateLimiter:
    mock_redis = MagicMock()
    mock_redis.evalsha = AsyncMock()
    mock_redis.script_load = AsyncMock(return_value="faksha")
    limiter = RateLimiter(windows)
    limiter._redis = mock_redis
    limiter._sha = "faksha"
    return limiter


@pytest.mark.asyncio
async def test_acquire_succeeds_when_slot_available():
    limiter = _make_limiter({"npm": [(60, 100)]})
    limiter._redis.evalsha = AsyncMock(return_value=1)
    await limiter.acquire("npm")
    limiter._redis.evalsha.assert_called_once()


@pytest.mark.asyncio
async def test_acquire_retries_when_no_slot():
    limiter = _make_limiter({"npm": [(60, 100)]})
    # First call: rejected (0 with wait hint), second call: accepted (1)
    limiter._redis.evalsha = AsyncMock(side_effect=[0, 1])
    limiter._redis.zrange = AsyncMock(return_value=[(b"id", time.time() + 0.05)])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.acquire("npm")
        mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_acquire_raises_for_unknown_group():
    limiter = _make_limiter({"npm": [(60, 100)]})
    with pytest.raises(KeyError):
        await limiter.acquire("github")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd worker && uv run pytest tests/unit/test_rate_limiter.py -v
```
Expected: failures — `RateLimiter` doesn't exist with new interface

- [ ] **Step 3: Rewrite `src/rate_limiter.py`**

```python
# src/rate_limiter.py
"""Redis-backed multi-window sliding rate limiter."""

import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis

from src.redis_client import get_client

logger = logging.getLogger(__name__)

# Lua script: atomically check all windows, consume if all have capacity.
# KEYS: one key per window  e.g. ["ratelimit:npm:60", "ratelimit:npm:3600"]
# ARGV: now, req_id, then pairs of window_secs,max_req per key
# Returns 1 if consumed, 0 if any window is full.
_LUA = """
local now = tonumber(ARGV[1])
local req_id = ARGV[2]
for i = 1, #KEYS do
    local window_secs = tonumber(ARGV[2 + (i-1)*2 + 1])
    local max_req     = tonumber(ARGV[2 + (i-1)*2 + 2])
    redis.call('ZREMRANGEBYSCORE', KEYS[i], 0, now - window_secs)
    if redis.call('ZCARD', KEYS[i]) >= max_req then
        return 0
    end
end
for i = 1, #KEYS do
    local window_secs = tonumber(ARGV[2 + (i-1)*2 + 1])
    redis.call('ZADD', KEYS[i], now, req_id)
    redis.call('EXPIRE', KEYS[i], window_secs + 1)
end
return 1
"""


class RateLimiter:
    def __init__(self, windows: dict[str, list[tuple[int, int]]]) -> None:
        """
        windows: maps rate_group -> list of (window_seconds, max_requests).
        Example: {"npm": [(60, 500), (3600, 5000)]}
        """
        self._windows = windows
        self._redis: aioredis.Redis | None = None
        self._sha: str | None = None

    async def _ensure_loaded(self) -> None:
        if self._sha is None:
            self._redis = get_client()
            self._sha = await self._redis.script_load(_LUA)

    async def acquire(self, rate_group: str) -> None:
        """Block until a request slot is available for rate_group."""
        windows = self._windows[rate_group]  # raises KeyError for unknown group
        await self._ensure_loaded()
        keys = [f"ratelimit:{rate_group}:{w}" for w, _ in windows]
        argv_pairs = [str(v) for w, m in windows for v in (w, m)]

        while True:
            now = time.time()
            req_id = str(uuid.uuid4())
            result = await self._redis.evalsha(
                self._sha, len(keys), *keys, str(now), req_id, *argv_pairs
            )
            if result == 1:
                return

            # Find earliest slot opening across all saturated windows
            wait = 1.0
            for key, (window_secs, _) in zip(keys, windows):
                oldest = await self._redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    _, score = oldest[0]
                    slot_open = score + window_secs - now
                    wait = max(wait, slot_open)

            logger.debug("rate limiter: %s throttled, waiting %.1fs", rate_group, wait)
            await asyncio.sleep(wait)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd worker && uv run pytest tests/unit/test_rate_limiter.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add worker/src/rate_limiter.py worker/tests/unit/test_rate_limiter.py
git commit -m "feat(worker): Redis multi-window sliding rate limiter"
```

---

### Task 5: Config updates

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Rewrite `src/config.py`**

```python
# src/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nats_url: str = "nats://localhost:4222"
    mongodb_uri: str = "mongodb://localhost:27017/misi"
    redis_url: str = "redis://localhost:6379"

    github_token: str = ""  # required for GitHub fetchers

    github_issues_lookback_days: int = 30
    github_releases_lookback_days: int = 90

    npm_rate_windows: list[tuple[int, int]] = [(60, 500), (3600, 5000)]
    github_rate_windows: list[tuple[int, int]] = [(60, 100), (3600, 5000)]

    worker_concurrency: int = 5
    max_retries: int = 3

    nats_max_deliver: int = 5
    nats_transient_backoff_base: float = 5.0
    nats_transient_backoff_cap: float = 300.0


settings = Settings()
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd worker && uv run pytest tests/unit/ -q
```
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add worker/src/config.py
git commit -m "feat(worker): update config for Redis, GitHub, multi-window rate limits"
```

---

### Task 6: FetcherEntry dataclass and public registry API

**Files:**
- Modify: `src/fetchers/__init__.py`

- [ ] **Step 1: Rewrite `src/fetchers/__init__.py`**

```python
# src/fetchers/__init__.py
"""Registry mapping entity_type -> FetcherEntry."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from src.rate_limiter import RateLimiter

type FetchFn = Callable[
    [httpx.AsyncClient, str, RateLimiter, int], Awaitable[dict]
]


@dataclass
class FetcherEntry:
    fetch_fn: FetchFn
    collection: str
    rate_group: str


def _build_registry() -> dict[str, FetcherEntry]:
    from src.fetchers.npm import fetch as npm_fetch
    from src.fetchers.github import (
        fetch_advisories,
        fetch_issues,
        fetch_releases,
    )
    return {
        "npm": FetcherEntry(npm_fetch, "npm_package_cache", "npm"),
        "github_issues": FetcherEntry(fetch_issues, "github_issues_cache", "github"),
        "github_releases": FetcherEntry(fetch_releases, "github_releases_cache", "github"),
        "github_advisories": FetcherEntry(fetch_advisories, "github_advisories_cache", "github"),
    }


_REGISTRY: dict[str, FetcherEntry] = {}


def _registry() -> dict[str, FetcherEntry]:
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _build_registry()
    return _REGISTRY


def get(entity_type: str) -> FetcherEntry:
    """Return FetcherEntry for the given entity type. Raises ValueError if unknown."""
    reg = _registry()
    if entity_type not in reg:
        raise ValueError(f"unknown entity type: {entity_type!r}")
    return reg[entity_type]


def entity_types() -> list[str]:
    return list(_registry().keys())


def rate_groups() -> set[str]:
    return {e.rate_group for e in _registry().values()}
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd worker && uv run pytest tests/unit/ -q
```
Expected: all pass (github.py doesn't exist yet — `_build_registry` is lazy so this is fine as long as no test imports github fetchers directly)

- [ ] **Step 3: Commit**

```bash
git add worker/src/fetchers/__init__.py
git commit -m "feat(worker): FetcherEntry dataclass, public registry API"
```

---

### Task 7: GitHub fetchers

**Files:**
- Create: `src/fetchers/github.py`
- Create: `tests/unit/test_github_fetcher.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_github_fetcher.py
import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.fetchers.github import fetch_advisories, fetch_issues, fetch_releases
from src.rate_limiter import RateLimiter


def _make_limiter() -> RateLimiter:
    limiter = MagicMock(spec=RateLimiter)
    limiter.acquire = AsyncMock()
    return limiter


def _resp(status: int, body=None, headers: dict | None = None, link: str | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body if body is not None else [])
    h = dict(headers or {})
    if link:
        h["link"] = link
    r.headers = h
    return r


@pytest.mark.asyncio
async def test_fetch_issues_returns_list():
    issue = {"id": 1, "title": "bug"}
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [issue]))
    limiter = _make_limiter()

    with MagicMock() as mock_settings:
        import src.fetchers.github as gh_module
        original = gh_module.settings
        gh_module.settings = MagicMock(
            github_token="tok",
            github_issues_lookback_days=30,
        )
        doc = await fetch_issues(client, "owner/repo", limiter, max_retries=3)
        gh_module.settings = original

    assert doc["issues"] == [issue]


@pytest.mark.asyncio
async def test_fetch_issues_raises_rate_limit_on_429():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(429, headers={"Retry-After": "30"}))
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(
        github_token="tok",
        github_issues_lookback_days=30,
    )
    with pytest.raises(RateLimitError) as exc_info:
        await fetch_issues(client, "owner/repo", limiter, max_retries=1)
    gh_module.settings = original
    assert exc_info.value.delay == 30.0


@pytest.mark.asyncio
async def test_fetch_releases_filters_by_date():
    now = datetime.now(UTC)
    old = (now - timedelta(days=100)).isoformat()
    recent = (now - timedelta(days=5)).isoformat()
    releases = [
        {"id": 1, "published_at": recent},
        {"id": 2, "published_at": old},
    ]
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, releases))
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(
        github_token="tok",
        github_releases_lookback_days=30,
    )
    doc = await fetch_releases(client, "owner/repo", limiter, max_retries=3)
    gh_module.settings = original

    assert len(doc["releases"]) == 1
    assert doc["releases"][0]["id"] == 1


@pytest.mark.asyncio
async def test_fetch_advisories_returns_list():
    advisory = {"ghsa_id": "GHSA-1234", "severity": "high"}
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [advisory]))
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(github_token="tok")
    doc = await fetch_advisories(client, "owner/repo", limiter, max_retries=3)
    gh_module.settings = original

    assert doc["advisories"] == [advisory]


@pytest.mark.asyncio
async def test_fetch_issues_paginates():
    page1 = [{"id": 1}]
    page2 = [{"id": 2}]
    link_header = '<https://api.github.com/next>; rel="next"'
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[
        _resp(200, page1, link=link_header),
        _resp(200, page2),
    ])
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(
        github_token="tok",
        github_issues_lookback_days=30,
    )
    doc = await fetch_issues(client, "owner/repo", limiter, max_retries=3)
    gh_module.settings = original

    assert len(doc["issues"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd worker && uv run pytest tests/unit/test_github_fetcher.py -v
```
Expected: `ModuleNotFoundError` for `src.fetchers.github`

- [ ] **Step 3: Create `src/fetchers/github.py`**

```python
# src/fetchers/github.py
"""GitHub REST API fetchers: issues, releases, security advisories."""

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx

from src.config import settings
from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"
_TIMEOUT = 20.0


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _next_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None


async def _get_pages(
    client: httpx.AsyncClient,
    url: str,
    rate_limiter: RateLimiter,
    max_retries: int,
) -> list[dict]:
    """Fetch all pages from a paginated GitHub endpoint."""
    results: list[dict] = []
    next_url: str | None = url

    while next_url:
        for attempt in range(max_retries):
            await rate_limiter.acquire("github")
            try:
                resp = await client.get(next_url, headers=_auth_headers(), timeout=_TIMEOUT)
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
                    raise TransientFetchError(str(exc)) from exc
                continue

            if resp.status_code == 200:
                page = resp.json()
                results.extend(page if isinstance(page, list) else [page])
                next_url = _next_url(resp.headers.get("link"))
                break
            if resp.status_code == 429:
                delay = float(resp.headers.get("Retry-After", 60))
                raise RateLimitError(delay)
            if resp.status_code == 404:
                raise PermanentFetchError(f"404 for {next_url}")
            if resp.status_code >= 500:
                if attempt == max_retries - 1:
                    raise TransientFetchError(f"status {resp.status_code} for {next_url}")
                continue
            raise PermanentFetchError(f"unexpected status {resp.status_code} for {next_url}")
        else:
            raise TransientFetchError(f"exhausted retries for {next_url}")

    return results


async def fetch_issues(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Fetch issues created/updated in the last github_issues_lookback_days days."""
    since = (
        datetime.now(UTC) - timedelta(days=settings.github_issues_lookback_days)
    ).isoformat()
    url = f"{_BASE}/repos/{name}/issues?state=all&since={since}&per_page=100"
    issues = await _get_pages(client, url, rate_limiter, max_retries)
    return {"issues": issues}


async def fetch_releases(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Fetch releases published in the last github_releases_lookback_days days."""
    url = f"{_BASE}/repos/{name}/releases?per_page=100"
    all_releases = await _get_pages(client, url, rate_limiter, max_retries)
    cutoff = datetime.now(UTC) - timedelta(days=settings.github_releases_lookback_days)
    recent = [
        r for r in all_releases
        if r.get("published_at")
        and datetime.fromisoformat(r["published_at"]) >= cutoff
    ]
    return {"releases": recent}


async def fetch_advisories(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Fetch all repository security advisories."""
    url = f"{_BASE}/repos/{name}/security-advisories?per_page=100"
    advisories = await _get_pages(client, url, rate_limiter, max_retries)
    return {"advisories": advisories}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd worker && uv run pytest tests/unit/test_github_fetcher.py -v
```
Expected: 5 passed

- [ ] **Step 5: Run all tests**

```bash
cd worker && uv run pytest tests/unit/ -q
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add worker/src/fetchers/github.py worker/tests/unit/test_github_fetcher.py
git commit -m "feat(worker): add GitHub issues, releases, advisories fetchers"
```

---

### Task 8: Consumer — ACK / NAK / term dispatch + type annotation

**Files:**
- Modify: `src/consumer.py`
- Create: `tests/unit/test_consumer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_consumer.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError


def _make_msg(num_delivered: int = 1):
    msg = MagicMock()
    msg.data = b'{"job_id":"j1","entity_type":"npm","name":"react"}'
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    msg.metadata = MagicMock()
    msg.metadata.num_delivered = num_delivered
    return msg


@pytest.mark.asyncio
async def test_process_success_acks_and_records_success():
    msg = _make_msg()
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(return_value={"registry_data": {}})
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer._save", new_callable=AsyncMock),
        patch("src.consumer.jobs.record_success", new_callable=AsyncMock) as mock_success,
    ):
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.ack.assert_called_once()
    mock_success.assert_called_once_with("j1")
    msg.nak.assert_not_called()
    msg.term.assert_not_called()


@pytest.mark.asyncio
async def test_process_rate_limit_naks_with_delay():
    msg = _make_msg()
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=RateLimitError(45.0))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
    ):
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.nak.assert_called_once_with(delay=45.0)
    msg.ack.assert_not_called()
    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_process_transient_error_naks_with_backoff():
    msg = _make_msg(num_delivered=2)
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=TransientFetchError("timeout"))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
        patch("src.consumer.settings") as mock_settings,
    ):
        mock_settings.nats_transient_backoff_base = 5.0
        mock_settings.nats_transient_backoff_cap = 300.0
        mock_settings.nats_max_deliver = 5
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.nak.assert_called_once()
    nak_delay = msg.nak.call_args.kwargs.get("delay") or msg.nak.call_args[1].get("delay")
    assert nak_delay > 0
    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_process_permanent_error_terms_and_records_failure():
    msg = _make_msg()
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=PermanentFetchError("404"))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
    ):
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.term.assert_called_once()
    msg.ack.assert_not_called()
    mock_fail.assert_called_once_with("j1")


@pytest.mark.asyncio
async def test_process_terms_when_max_deliver_exhausted():
    msg = _make_msg(num_delivered=5)  # equals nats_max_deliver
    mock_entry = MagicMock()
    mock_entry.fetch_fn = AsyncMock(side_effect=TransientFetchError("timeout"))
    mock_entry.collection = "npm_package_cache"
    mock_entry.rate_group = "npm"

    with (
        patch("src.consumer.fetchers.get", return_value=mock_entry),
        patch("src.consumer.jobs.record_failure", new_callable=AsyncMock) as mock_fail,
        patch("src.consumer.settings") as mock_settings,
    ):
        mock_settings.nats_transient_backoff_base = 5.0
        mock_settings.nats_transient_backoff_cap = 300.0
        mock_settings.nats_max_deliver = 5
        from src.consumer import _process
        limiter = MagicMock()
        await _process(msg, AsyncMock(), limiter)

    msg.term.assert_called_once()
    mock_fail.assert_called_once_with("j1")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd worker && uv run pytest tests/unit/test_consumer.py -v
```
Expected: failures — `_process` signature doesn't match

- [ ] **Step 3: Rewrite `src/consumer.py`**

```python
# src/consumer.py
"""NATS JetStream pull-consumer — dispatches to fetcher by entity type."""

import asyncio
import json
import logging
import math
from datetime import UTC, datetime

import httpx
from nats.aio.msg import Msg
from nats.js.api import ConsumerConfig
from nats.js.errors import FetchTimeoutError

from src import fetchers, jobs
from src.config import settings
from src.db import get_db
from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.nats_client import STREAM_NAME, get_js
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


async def _save(collection: str, name: str, doc: dict) -> None:
    await get_db()[collection].replace_one(
        {"name": name},
        {"name": name, "fetched_at": datetime.now(UTC), **doc},
        upsert=True,
    )


def _backoff(num_delivered: int) -> float:
    delay = settings.nats_transient_backoff_base * (2 ** (num_delivered - 1))
    return min(delay, settings.nats_transient_backoff_cap)


async def _process(
    msg: Msg,
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter,
) -> None:
    data = json.loads(msg.data)
    job_id: str = data["job_id"]
    entity_type: str = data["entity_type"]
    name: str = data["name"]
    num_delivered: int = msg.metadata.num_delivered

    try:
        entry = fetchers.get(entity_type)
        doc = await entry.fetch_fn(client, name, rate_limiter, settings.max_retries)
        await _save(entry.collection, name, doc)
        await jobs.record_success(job_id)
        await msg.ack()

    except RateLimitError as exc:
        logger.warning("consumer: rate limited %s/%s, requeue in %.0fs", entity_type, name, exc.delay)
        await msg.nak(delay=exc.delay)

    except TransientFetchError as exc:
        if num_delivered >= settings.nats_max_deliver:
            logger.error("consumer: exhausted retries %s/%s: %s", entity_type, name, exc)
            await msg.term()
            await jobs.record_failure(job_id)
        else:
            delay = _backoff(num_delivered)
            logger.warning("consumer: transient error %s/%s, requeue in %.0fs", entity_type, name, delay)
            await msg.nak(delay=delay)

    except PermanentFetchError as exc:
        logger.error("consumer: permanent error %s/%s: %s", entity_type, name, exc)
        await msg.term()
        await jobs.record_failure(job_id)

    except Exception as exc:
        logger.error("consumer: unexpected error %s/%s: %s", entity_type, name, exc)
        await msg.term()
        await jobs.record_failure(job_id)


async def _worker(
    sub: "nats.js.client.JetStreamContext.PullSubscription",
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter,
) -> None:
    while True:
        try:
            msgs = await sub.fetch(1, timeout=1.0)
            for msg in msgs:
                await _process(msg, client, rate_limiter)
        except asyncio.CancelledError:
            break
        except FetchTimeoutError:
            pass
        except Exception:
            logger.warning("worker: fetch error", exc_info=True)
            await asyncio.sleep(0.1)


async def run_consumer(rate_limiter: RateLimiter) -> None:
    js = get_js()
    sub = await js.pull_subscribe(
        "entity.fetch.*",
        durable="entity-worker",
        stream=STREAM_NAME,
        config=ConsumerConfig(max_deliver=settings.nats_max_deliver),
    )
    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(_worker(sub, client, rate_limiter))
            for _ in range(settings.worker_concurrency)
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd worker && uv run pytest tests/unit/test_consumer.py -v
```
Expected: 5 passed

- [ ] **Step 5: Run all tests**

```bash
cd worker && uv run pytest tests/unit/ -q
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add worker/src/consumer.py worker/tests/unit/test_consumer.py
git commit -m "feat(worker): ack/nak/term dispatch, typed errors, RateLimiter in consumer"
```

---

### Task 9: Update main.py — derive rate groups from registry, init Redis

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Rewrite `src/main.py`**

```python
# src/main.py
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import fetchers
from src.config import settings
from src.consumer import run_consumer
from src.nats_client import close as nats_close
from src.nats_client import connect as nats_connect
from src.rate_limiter import RateLimiter
from src.redis_client import close as redis_close
from src.redis_client import connect as redis_connect
from src.routers import ingest

logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task | None = None

_RATE_CONFIGS: dict[str, list[tuple[int, int]]] = {
    "npm": settings.npm_rate_windows,
    "github": settings.github_rate_windows,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task
    await nats_connect()
    await redis_connect()

    missing = fetchers.rate_groups() - set(_RATE_CONFIGS)
    if missing:
        raise ValueError(f"No rate config for groups: {missing}")

    windows = {group: _RATE_CONFIGS[group] for group in fetchers.rate_groups()}
    rate_limiter = RateLimiter(windows)

    _consumer_task = asyncio.create_task(run_consumer(rate_limiter))
    logger.info("entity-worker started")
    yield

    if _consumer_task:
        _consumer_task.cancel()
        await asyncio.gather(_consumer_task, return_exceptions=True)
    await nats_close()
    await redis_close()
    logger.info("entity-worker stopped")


app = FastAPI(title="entity-worker", lifespan=lifespan)
app.include_router(ingest.router)
```

- [ ] **Step 2: Run all tests**

```bash
cd worker && uv run pytest tests/unit/ -q
```
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add worker/src/main.py
git commit -m "feat(worker): derive rate groups from registry, init Redis in lifespan"
```

---

### Task 10: Pydantic model validator in ingest router

**Files:**
- Modify: `src/routers/ingest.py`
- Modify: `tests/unit/test_routes.py`

- [ ] **Step 1: Read the current test file to understand what needs updating**

```bash
cat worker/tests/unit/test_routes.py
```

- [ ] **Step 2: Rewrite `src/routers/ingest.py`**

```python
# src/routers/ingest.py
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from src import fetchers, jobs
from src.nats_client import get_js, subject_for

router = APIRouter()


class IngestRequest(BaseModel):
    entity_type: str
    items: Annotated[list[str], Field(min_length=1)]

    @model_validator(mode="after")
    def check_entity_type(self) -> "IngestRequest":
        known = fetchers.entity_types()
        if self.entity_type not in known:
            raise ValueError(
                f"unknown entity_type {self.entity_type!r}, must be one of {sorted(known)}"
            )
        return self


class IngestResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    failed: int


@router.post("/ingest", status_code=201, response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    job_id = str(uuid.uuid4())
    await jobs.create(job_id, body.items)
    js = get_js()
    subject = subject_for(body.entity_type)
    try:
        for name in body.items:
            payload = json.dumps(
                {"job_id": job_id, "entity_type": body.entity_type, "name": name}
            ).encode()
            await js.publish(subject, payload)
    except Exception:
        await jobs.delete(job_id)
        raise HTTPException(status_code=503, detail="failed to enqueue job")
    return IngestResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(job_id: str) -> StatusResponse:
    status = await jobs.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return StatusResponse(**status)
```

- [ ] **Step 3: Rewrite `tests/unit/test_routes.py`** (fix deferred imports, add validator test)

```python
# tests/unit/test_routes.py
import pytest
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_ingest_returns_job_id():
    with (
        patch("src.routers.ingest.jobs.create", new_callable=AsyncMock),
        patch("src.routers.ingest.get_js") as mock_js,
    ):
        mock_js.return_value.publish = AsyncMock()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/ingest",
                json={"entity_type": "npm", "items": ["react", "lodash"]},
            )

    assert resp.status_code == 201
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str)


@pytest.mark.asyncio
async def test_ingest_rejects_unknown_entity_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/ingest",
            json={"entity_type": "unknown_type", "items": ["foo"]},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_status_returns_404_for_unknown_job():
    with patch("src.routers.ingest.jobs.get_status", new_callable=AsyncMock, return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/status/unknown-job-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_status_returns_progress():
    status_data = {
        "job_id": "job-1",
        "status": "running",
        "total": 10,
        "completed": 5,
        "failed": 0,
    }
    with patch("src.routers.ingest.jobs.get_status", new_callable=AsyncMock, return_value=status_data):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/status/job-1")
    assert resp.status_code == 200
    assert resp.json()["completed"] == 5
```

- [ ] **Step 4: Run all tests**

```bash
cd worker && uv run pytest tests/unit/ -q
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add worker/src/routers/ingest.py worker/tests/unit/test_routes.py
git commit -m "feat(worker): model_validator for entity_type, fix deferred test imports in routes"
```

---

### Task 11: Fix remaining deferred test imports

**Files:**
- Modify: `tests/unit/test_jobs.py`

- [ ] **Step 1: Rewrite `tests/unit/test_jobs.py`** (move imports to top)

```python
# tests/unit/test_jobs.py
import pytest
from unittest.mock import AsyncMock, patch

from src.jobs import create, delete, get_status, record_failure, record_success


async def test_create_inserts_correct_document():
    mock_col = AsyncMock()
    with patch("src.jobs._col", return_value=mock_col):
        await create("job-1", ["react", "lodash"])

    mock_col.insert_one.assert_called_once()
    doc = mock_col.insert_one.call_args[0][0]
    assert doc["_id"] == "job-1"
    assert doc["packages"] == ["react", "lodash"]
    assert doc["total"] == 2
    assert doc["completed"] == 0
    assert doc["failed"] == 0
    assert doc["status"] == "pending"
    assert "created_at" in doc
    assert "updated_at" in doc


async def test_record_success_always_calls_update_one():
    mock_col = AsyncMock()
    mock_col.find_one_and_update = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        await record_success("job-1")
    mock_col.update_one.assert_called_once()


async def test_record_failure_always_calls_update_one():
    mock_col = AsyncMock()
    mock_col.find_one_and_update = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        await record_failure("job-1")
    mock_col.update_one.assert_called_once()


async def test_get_status_returns_none_for_missing_job():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        result = await get_status("missing")
    assert result is None


async def test_get_status_returns_dict():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(
        return_value={"status": "done", "total": 2, "completed": 2, "failed": 0}
    )
    with patch("src.jobs._col", return_value=mock_col):
        result = await get_status("job-1")
    assert result == {"job_id": "job-1", "status": "done", "total": 2, "completed": 2, "failed": 0}


async def test_get_status_uses_projection():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        await get_status("job-1")
    call_args = mock_col.find_one.call_args
    projection = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("projection")
    assert projection is not None
    assert "_id" not in projection or projection.get("_id") == 0
```

- [ ] **Step 2: Run all tests**

```bash
cd worker && uv run pytest tests/unit/ -q
```
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add worker/tests/unit/test_jobs.py
git commit -m "fix(worker): move deferred imports to module level in test_jobs"
```

---

### Task 12: Docker Compose

**Files:**
- Create: `worker/docker-compose.yml`

- [ ] **Step 1: Create `worker/docker-compose.yml`**

```yaml
services:
  nats:
    image: nats:latest
    command: ["-js", "-m", "8222"]
    ports:
      - "4222:4222"
      - "8222:8222"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

- [ ] **Step 2: Smoke test**

```bash
cd worker && docker compose up -d
docker compose ps
```
Expected: both services `running`

```bash
cd worker && docker compose down
```

- [ ] **Step 3: Commit**

```bash
git add worker/docker-compose.yml
git commit -m "feat(worker): add Docker Compose with NATS JetStream and Redis"
```

---

### Task 13: Final verification

- [ ] **Step 1: Run the full test suite**

```bash
cd worker && uv run pytest tests/unit/ -v
```
Expected: all tests pass, no warnings about deferred imports

- [ ] **Step 2: Check for stray `_REGISTRY` or `TokenBucket` references**

```bash
grep -r "TokenBucket\|_REGISTRY" worker/src/
```
Expected: no output

- [ ] **Step 3: Verify the registry public API is used everywhere**

```bash
grep -r "fetchers\." worker/src/ | grep -v "fetchers\.get\|fetchers\.entity_types\|fetchers\.rate_groups\|fetchers/__init__\|fetchers/errors\|fetchers/npm\|fetchers/github"
```
Expected: no output (no code reaches into fetchers internals)

- [ ] **Step 4: Final commit**

```bash
git add -p  # stage any stragglers
git commit -m "chore(worker): final cleanup"
```
