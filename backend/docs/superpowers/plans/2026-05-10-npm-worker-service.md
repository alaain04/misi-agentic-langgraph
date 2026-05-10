# Entity Ingestor Worker Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone FastAPI + NATS JetStream worker that handles external data fetching for multiple entity types (npm packages, GitHub repos, etc.) with per-type rate limiting and retry, and update the LangGraph backend to delegate npm fetching to this worker instead of calling npm APIs directly.

**Architecture:** A new FastAPI process (`apps/v3/langgraph/worker/`) exposes `POST /ingest` (accepts `entity_type` + list of item names) and `GET /status/{job_id}`. LangGraph analyze nodes post a list of names with an entity type, then poll for completion. The worker publishes one NATS message per item to a per-type subject (`entity.fetch.npm`, `entity.fetch.github`, …) on a shared JetStream stream. N concurrent pull consumers drain the queue, dispatch to the correct fetcher module by entity type, apply a per-type token-bucket rate limiter, and retry on 429 with exponential backoff. Fetched data is written to the appropriate MongoDB collection (e.g. `npm_package_cache`). After the job is done, LangGraph nodes read from that collection directly. Job progress is tracked in a MongoDB `ingest_jobs` collection.

**Tech Stack:** Python 3.12, FastAPI, nats-py ≥2.9, pymongo async, httpx, pydantic-settings, pytest-asyncio, ruff

---

## File Structure

### New: `apps/v3/langgraph/worker/`

| File | Purpose |
|------|---------|
| `pyproject.toml` | Project deps and tooling config |
| `src/__init__.py` | Empty package marker |
| `src/config.py` | pydantic-settings (NATS_URL, MONGODB_URI, NPM_RATE_LIMIT_RPS, WORKER_CONCURRENCY, MAX_RETRIES) |
| `src/db.py` | Async pymongo connection (same pattern as backend) |
| `src/nats_client.py` | NATS connect, JetStream stream creation, `subject_for(entity_type)`, `get_js()` |
| `src/rate_limiter.py` | Async token-bucket (one instance per entity type) |
| `src/fetchers/__init__.py` | Registry: `entity_type → (fetch_fn, cache_collection)` |
| `src/fetchers/npm.py` | Fetch npm registry metadata + weekly downloads; retry on 429/5xx |
| `src/jobs.py` | CRUD for `ingest_jobs` MongoDB collection |
| `src/consumer.py` | NATS pull-consumer; dispatches to fetcher by entity type |
| `src/routers/__init__.py` | Empty |
| `src/routers/ingest.py` | POST /ingest, GET /status/{job_id} |
| `src/main.py` | FastAPI app + lifespan (connect NATS, start consumers) |
| `tests/__init__.py` | Empty |
| `tests/unit/__init__.py` | Empty |
| `tests/unit/test_rate_limiter.py` | Token-bucket behavior |
| `tests/unit/test_npm_fetcher.py` | npm retry logic with mocked httpx |
| `tests/unit/test_jobs.py` | Job state transitions |
| `tests/unit/test_routes.py` | Endpoint contracts |

### Modified: `apps/v3/langgraph/backend/`

| File | Change |
|------|--------|
| `src/utils/config.py` | Add `npm_worker_url: str` field |
| `src/services/npm_registry_cache.py` | **DELETE** |
| `src/services/npm_cache.py` | **NEW** — read-only `NpmPackageCacheEntry` model + `get_cached()` |
| `src/services/npm_ingestor_client.py` | **NEW** — `ingest()` and `wait()` HTTP client functions |
| `src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py` | Use ingestor client + cache reader |
| `src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness/nodes/analyze.py` | Use ingestor client + cache reader |

---

## Tasks

### Task 1: Worker project scaffold

**Files:**
- Create: `apps/v3/langgraph/worker/pyproject.toml`
- Create: `apps/v3/langgraph/worker/src/__init__.py`
- Create: `apps/v3/langgraph/worker/src/config.py`
- Create: `apps/v3/langgraph/worker/src/db.py`
- Create: `apps/v3/langgraph/worker/tests/__init__.py`
- Create: `apps/v3/langgraph/worker/tests/unit/__init__.py`

- [ ] **Step 1: Create the project directory and pyproject.toml**

```bash
mkdir -p apps/v3/langgraph/worker/src/routers
mkdir -p apps/v3/langgraph/worker/tests/unit
```

Create `apps/v3/langgraph/worker/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "npm-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.136.1",
    "nats-py>=2.9.0",
    "pymongo>=4.17.0",
    "httpx>=0.27.0",
    "pydantic-settings>=2.14.0",
    "uvicorn[standard]>=0.46.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.9.0",
]

[tool.hatch.build.targets.wheel]
packages = ["src"]

[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Install dependencies**

Run from `apps/v3/langgraph/worker/`:

```bash
uv sync
```

Expected: lock file created, all packages installed.

- [ ] **Step 3: Create src/__init__.py**

Create `apps/v3/langgraph/worker/src/__init__.py` (empty file).

- [ ] **Step 4: Write config.py**

Create `apps/v3/langgraph/worker/src/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nats_url: str = "nats://localhost:4222"
    mongodb_uri: str = "mongodb://localhost:27017/misi"
    npm_rate_limit_rps: float = 10.0
    github_rate_limit_rps: float = 5.0
    worker_concurrency: int = 5
    max_retries: int = 3


settings = Settings()
```

- [ ] **Step 5: Write db.py**

Create `apps/v3/langgraph/worker/src/db.py`:

```python
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from src.config import settings

_client: AsyncMongoClient | None = None


def get_client() -> AsyncMongoClient:
    global _client
    if _client is None:
        _client = AsyncMongoClient(settings.mongodb_uri)
    return _client


def get_db() -> AsyncDatabase:
    return get_client().get_database()
```

- [ ] **Step 6: Create empty test files**

Create `apps/v3/langgraph/worker/tests/__init__.py` (empty).
Create `apps/v3/langgraph/worker/tests/unit/__init__.py` (empty).

- [ ] **Step 7: Verify ruff passes**

```bash
uv run ruff check src/
```

Expected: no output (no errors).

- [ ] **Step 8: Commit**

```bash
git add apps/v3/langgraph/worker/
git commit -m "feat(npm-worker): scaffold project with config and db"
```

---

### Task 2: NATS client

**Files:**
- Create: `apps/v3/langgraph/worker/src/nats_client.py`

- [ ] **Step 1: Write nats_client.py**

Create `apps/v3/langgraph/worker/src/nats_client.py`:

```python
import logging

import nats
from nats.aio.client import Client
from nats.js import JetStreamContext
from nats.js.api import RetentionPolicy, StreamConfig

from src.config import settings

logger = logging.getLogger(__name__)

STREAM_NAME = "ENTITY_FETCH"
_SUBJECT_PREFIX = "entity.fetch"


def subject_for(entity_type: str) -> str:
    return f"{_SUBJECT_PREFIX}.{entity_type}"


_nc: Client | None = None
_js: JetStreamContext | None = None


async def connect() -> None:
    global _nc, _js
    _nc = await nats.connect(settings.nats_url)
    _js = _nc.jetstream()
    try:
        await _js.add_stream(
            StreamConfig(
                name=STREAM_NAME,
                subjects=[f"{_SUBJECT_PREFIX}.*"],
                retention=RetentionPolicy.WORK_QUEUE,
            )
        )
        logger.info("nats: stream %s created", STREAM_NAME)
    except Exception:
        logger.debug("nats: stream %s already exists", STREAM_NAME)


async def close() -> None:
    if _nc:
        await _nc.drain()


def get_js() -> JetStreamContext:
    if _js is None:
        raise RuntimeError("NATS not connected — call connect() first")
    return _js
```

- [ ] **Step 2: Verify ruff**

```bash
uv run ruff check src/nats_client.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add apps/v3/langgraph/worker/src/nats_client.py
git commit -m "feat(npm-worker): add NATS JetStream client"
```

---

### Task 3: Rate limiter

**Files:**
- Create: `apps/v3/langgraph/worker/src/rate_limiter.py`
- Test: `apps/v3/langgraph/worker/tests/unit/test_rate_limiter.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/v3/langgraph/worker/tests/unit/test_rate_limiter.py`:

```python
import asyncio
import time

import pytest

from src.rate_limiter import TokenBucket


async def test_single_acquire_does_not_block():
    bucket = TokenBucket(rate=10.0)
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


async def test_rate_is_applied():
    bucket = TokenBucket(rate=5.0)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    # 5 tokens at 5/s: first is free, next 4 cost 0.2s each → ~0.8s
    assert elapsed >= 0.7


async def test_tokens_refill_over_time():
    bucket = TokenBucket(rate=10.0)
    # drain all tokens
    for _ in range(10):
        await bucket.acquire()
    # wait for refill
    await asyncio.sleep(0.5)
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1
```

- [ ] **Step 2: Run tests — expect failure**

```bash
uv run pytest tests/unit/test_rate_limiter.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.rate_limiter'`

- [ ] **Step 3: Implement rate_limiter.py**

Create `apps/v3/langgraph/worker/src/rate_limiter.py`:

```python
import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._tokens = rate
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(
                    self._rate, self._tokens + elapsed * self._rate
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
uv run pytest tests/unit/test_rate_limiter.py -v
```

Expected: 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/rate_limiter.py tests/unit/test_rate_limiter.py
git commit -m "feat(npm-worker): add async token-bucket rate limiter"
```

---

### Task 4: Fetcher modules

**Files:**
- Create: `apps/v3/langgraph/worker/src/fetchers/__init__.py`
- Create: `apps/v3/langgraph/worker/src/fetchers/npm.py`
- Test: `apps/v3/langgraph/worker/tests/unit/test_npm_fetcher.py`

Each fetcher module exposes a single `async def fetch(client, name, bucket, max_retries) -> dict` function. The return value is the document stored verbatim in the MongoDB cache collection. The fetcher registry in `__init__.py` maps `entity_type → (fetch_fn, cache_collection_name)`.

- [ ] **Step 1: Write the failing tests**

Create `apps/v3/langgraph/worker/tests/unit/test_npm_fetcher.py`:

```python
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.fetchers.npm import fetch
from src.rate_limiter import TokenBucket


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


def _429(retry_after: str = "0") -> httpx.Response:
    return httpx.Response(429, headers={"Retry-After": retry_after}, json={})


async def test_fetch_returns_data_on_200():
    bucket = TokenBucket(rate=100.0)
    registry = {"name": "react", "dist-tags": {"latest": "18.0.0"}}
    downloads = {"downloads": 5_000_000}

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        side_effect=[_resp(200, registry), _resp(200, downloads)]
    )

    result = await fetch(client, "react", bucket)

    assert result["registry_data"] == registry
    assert result["weekly_downloads"] == 5_000_000


async def test_fetch_returns_empty_on_404():
    bucket = TokenBucket(rate=100.0)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=[_resp(404, {}), _resp(404, {})])

    result = await fetch(client, "nonexistent-pkg-xyz", bucket)

    assert result["registry_data"] == {}
    assert result["weekly_downloads"] is None


async def test_fetch_retries_on_429():
    bucket = TokenBucket(rate=100.0)
    registry = {"name": "lodash"}
    downloads = {"downloads": 1_000}

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        side_effect=[
            _429(),
            _resp(200, registry),
            _resp(200, downloads),
        ]
    )

    with patch("src.fetchers.npm.asyncio.sleep", new_callable=AsyncMock):
        result = await fetch(client, "lodash", bucket, max_retries=2)

    assert result["registry_data"] == registry


async def test_fetch_returns_empty_after_max_retries():
    bucket = TokenBucket(rate=100.0)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_429())

    with patch("src.fetchers.npm.asyncio.sleep", new_callable=AsyncMock):
        result = await fetch(client, "react", bucket, max_retries=2)

    assert result["registry_data"] == {}
    assert result["weekly_downloads"] is None
```

- [ ] **Step 2: Run tests — expect failure**

```bash
uv run pytest tests/unit/test_npm_fetcher.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.fetchers'`

- [ ] **Step 3: Create fetchers/npm.py**

```bash
mkdir -p apps/v3/langgraph/worker/src/fetchers
```

Create `apps/v3/langgraph/worker/src/fetchers/npm.py`:

```python
"""Fetch npm registry metadata and weekly downloads with retry on 429."""

import asyncio
import logging
import urllib.parse

import httpx

from src.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
_TIMEOUT = 15.0


def _encode(name: str) -> str:
    return urllib.parse.quote(name, safe="")


async def _get(
    client: httpx.AsyncClient,
    url: str,
    bucket: TokenBucket,
    max_retries: int,
) -> httpx.Response | None:
    delay = 1.0
    for _ in range(max_retries):
        await bucket.acquire()
        try:
            resp = await client.get(url, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", delay))
                logger.debug("npm: 429 on %s, retry in %.1fs", url, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            return None
        except httpx.RequestError as exc:
            logger.debug("npm: request error %s: %s", url, exc)
            await asyncio.sleep(delay)
            delay *= 2
    return None


async def fetch(
    client: httpx.AsyncClient,
    name: str,
    bucket: TokenBucket,
    max_retries: int = 3,
) -> dict:
    """Return cache document dict for one npm package."""
    encoded = _encode(name)
    reg_resp, dl_resp = await asyncio.gather(
        _get(client, f"{_NPM_REGISTRY}/{encoded}", bucket, max_retries),
        _get(client, f"{_NPM_DOWNLOADS}/{encoded}", bucket, max_retries),
    )
    return {
        "registry_data": reg_resp.json() if reg_resp else {},
        "weekly_downloads": (
            dl_resp.json().get("downloads") if dl_resp else None
        ),
    }
```

- [ ] **Step 4: Create fetchers/__init__.py (the registry)**

Create `apps/v3/langgraph/worker/src/fetchers/__init__.py`:

```python
"""Registry mapping entity_type → (fetch_fn, cache_collection)."""

from collections.abc import Callable

import httpx

from src.fetchers.npm import fetch as npm_fetch
from src.rate_limiter import TokenBucket

type FetchFn = Callable[
    [httpx.AsyncClient, str, TokenBucket, int], object
]

_REGISTRY: dict[str, tuple[FetchFn, str]] = {
    "npm": (npm_fetch, "npm_package_cache"),
}


def get(entity_type: str) -> tuple[FetchFn, str]:
    """Return (fetch_fn, cache_collection) for the given entity type."""
    if entity_type not in _REGISTRY:
        raise ValueError(f"unknown entity type: {entity_type!r}")
    return _REGISTRY[entity_type]
```

- [ ] **Step 5: Run tests — expect pass**

```bash
uv run pytest tests/unit/test_npm_fetcher.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/fetchers/ tests/unit/test_npm_fetcher.py
git commit -m "feat(worker): add fetcher registry with npm module"
```

---

### Task 5: Job tracking

**Files:**
- Create: `apps/v3/langgraph/worker/src/jobs.py`
- Test: `apps/v3/langgraph/worker/tests/unit/test_jobs.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/v3/langgraph/worker/tests/unit/test_jobs.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_create_inserts_correct_document():
    mock_col = AsyncMock()
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import create
        await create("job-1", ["react", "lodash"])

    mock_col.insert_one.assert_called_once()
    doc = mock_col.insert_one.call_args[0][0]
    assert doc["_id"] == "job-1"
    assert doc["packages"] == ["react", "lodash"]
    assert doc["total"] == 2
    assert doc["completed"] == 0
    assert doc["failed"] == 0
    assert doc["status"] == "pending"


async def test_record_success_sets_done_when_all_complete():
    mock_col = AsyncMock()
    # find_one_and_update returns the updated doc (after increment)
    mock_col.find_one_and_update = AsyncMock(
        return_value={"completed": 1, "failed": 0, "total": 1}
    )
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import record_success
        await record_success("job-1")

    # second update sets status=done
    mock_col.update_one.assert_called_once()
    update_args = mock_col.update_one.call_args[0]
    assert update_args[1]["$set"]["status"] == "done"


async def test_record_success_does_not_set_done_when_incomplete():
    mock_col = AsyncMock()
    mock_col.find_one_and_update = AsyncMock(
        return_value={"completed": 1, "failed": 0, "total": 3}
    )
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import record_success
        await record_success("job-1")

    mock_col.update_one.assert_not_called()


async def test_get_status_returns_none_for_missing_job():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import get_status
        result = await get_status("missing")

    assert result is None


async def test_get_status_returns_dict():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(
        return_value={
            "_id": "job-1",
            "status": "done",
            "total": 2,
            "completed": 2,
            "failed": 0,
        }
    )
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import get_status
        result = await get_status("job-1")

    assert result == {
        "job_id": "job-1",
        "status": "done",
        "total": 2,
        "completed": 2,
        "failed": 0,
    }
```

- [ ] **Step 2: Run tests — expect failure**

```bash
uv run pytest tests/unit/test_jobs.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.jobs'`

- [ ] **Step 3: Implement jobs.py**

Create `apps/v3/langgraph/worker/src/jobs.py`:

```python
from datetime import UTC, datetime

from pymongo.asynchronous.collection import AsyncCollection

from src.db import get_db

_COLLECTION = "ingest_jobs"


def _col() -> AsyncCollection:
    return get_db()[_COLLECTION]


async def create(job_id: str, packages: list[str]) -> None:
    now = datetime.now(UTC)
    await _col().insert_one(
        {
            "_id": job_id,
            "packages": packages,
            "total": len(packages),
            "completed": 0,
            "failed": 0,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }
    )


async def record_success(job_id: str) -> None:
    doc = await _col().find_one_and_update(
        {"_id": job_id},
        {
            "$inc": {"completed": 1},
            "$set": {"status": "running", "updated_at": datetime.now(UTC)},
        },
        return_document=True,
    )
    if doc and doc["completed"] + doc["failed"] >= doc["total"]:
        await _col().update_one(
            {"_id": job_id},
            {"$set": {"status": "done", "updated_at": datetime.now(UTC)}},
        )


async def record_failure(job_id: str) -> None:
    doc = await _col().find_one_and_update(
        {"_id": job_id},
        {
            "$inc": {"failed": 1},
            "$set": {"status": "running", "updated_at": datetime.now(UTC)},
        },
        return_document=True,
    )
    if doc and doc["completed"] + doc["failed"] >= doc["total"]:
        await _col().update_one(
            {"_id": job_id},
            {"$set": {"status": "done", "updated_at": datetime.now(UTC)}},
        )


async def get_status(job_id: str) -> dict | None:
    doc = await _col().find_one({"_id": job_id})
    if not doc:
        return None
    return {
        "job_id": job_id,
        "status": doc["status"],
        "total": doc["total"],
        "completed": doc["completed"],
        "failed": doc["failed"],
    }
```

- [ ] **Step 4: Run tests — expect pass**

```bash
uv run pytest tests/unit/test_jobs.py -v
```

Expected: 5 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/jobs.py tests/unit/test_jobs.py
git commit -m "feat(npm-worker): add job tracking with MongoDB"
```

---

### Task 6: NATS consumer

**Files:**
- Create: `apps/v3/langgraph/worker/src/consumer.py`

- [ ] **Step 1: Implement consumer.py**

Create `apps/v3/langgraph/worker/src/consumer.py`:

```python
"""NATS JetStream pull-consumer — dispatches to fetcher by entity type."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
from nats.aio.msg import Msg

from src import fetchers, jobs
from src.config import settings
from src.db import get_db
from src.nats_client import STREAM_NAME, get_js
from src.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)


async def _save(collection: str, name: str, doc: dict) -> None:
    await get_db()[collection].replace_one(
        {"name": name},
        {"name": name, "fetched_at": datetime.now(UTC), **doc},
        upsert=True,
    )


async def _process(
    msg: Msg,
    client: httpx.AsyncClient,
    buckets: dict[str, TokenBucket],
) -> None:
    data = json.loads(msg.data)
    job_id: str = data["job_id"]
    entity_type: str = data["entity_type"]
    name: str = data["name"]
    try:
        fetch_fn, collection = fetchers.get(entity_type)
        bucket = buckets[entity_type]
        doc = await fetch_fn(client, name, bucket, settings.max_retries)
        await _save(collection, name, doc)
        await jobs.record_success(job_id)
        await msg.ack()
    except Exception as exc:
        logger.error("consumer: failed %s/%s: %s", entity_type, name, exc)
        await jobs.record_failure(job_id)
        await msg.ack()


async def _worker(
    sub,
    client: httpx.AsyncClient,
    buckets: dict[str, TokenBucket],
) -> None:
    while True:
        try:
            msgs = await sub.fetch(1, timeout=1.0)
            for msg in msgs:
                await _process(msg, client, buckets)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.1)


async def run_consumer(buckets: dict[str, TokenBucket]) -> None:
    js = get_js()
    sub = await js.pull_subscribe(
        f"entity.fetch.*", durable="entity-worker", stream=STREAM_NAME
    )
    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(_worker(sub, client, buckets))
            for _ in range(settings.worker_concurrency)
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
```

- [ ] **Step 2: Verify ruff**

```bash
uv run ruff check src/consumer.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/consumer.py
git commit -m "feat(npm-worker): add NATS pull-consumer with cache write"
```

---

### Task 7: FastAPI app and routes

**Files:**
- Create: `apps/v3/langgraph/worker/src/routers/__init__.py`
- Create: `apps/v3/langgraph/worker/src/routers/ingest.py`
- Create: `apps/v3/langgraph/worker/src/main.py`
- Test: `apps/v3/langgraph/worker/tests/unit/test_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/v3/langgraph/worker/tests/unit/test_routes.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


async def test_ingest_returns_job_id():
    with (
        patch("src.routers.ingest.jobs.create", new_callable=AsyncMock),
        patch("src.routers.ingest.get_js") as mock_js,
    ):
        mock_js.return_value.publish = AsyncMock()
        from src.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/ingest",
                json={"entity_type": "npm", "items": ["react", "lodash"]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str)


async def test_status_returns_404_for_unknown_job():
    with patch(
        "src.routers.ingest.jobs.get_status",
        new_callable=AsyncMock,
        return_value=None,
    ):
        from src.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/status/unknown-job-id")

    assert resp.status_code == 404


async def test_status_returns_progress():
    status_data = {
        "job_id": "job-1",
        "status": "running",
        "total": 10,
        "completed": 5,
        "failed": 0,
    }
    with patch(
        "src.routers.ingest.jobs.get_status",
        new_callable=AsyncMock,
        return_value=status_data,
    ):
        from src.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/status/job-1")

    assert resp.status_code == 200
    assert resp.json()["completed"] == 5
```

- [ ] **Step 2: Run tests — expect failure**

```bash
uv run pytest tests/unit/test_routes.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.main'`

- [ ] **Step 3: Create routers/__init__.py**

Create `apps/v3/langgraph/worker/src/routers/__init__.py` (empty).

- [ ] **Step 4: Implement routers/ingest.py**

Create `apps/v3/langgraph/worker/src/routers/ingest.py`:

```python
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import jobs
from src.nats_client import get_js, subject_for

router = APIRouter()


class IngestRequest(BaseModel):
    entity_type: str
    items: list[str]


class IngestResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    failed: int


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    job_id = str(uuid.uuid4())
    await jobs.create(job_id, body.items)
    js = get_js()
    subject = subject_for(body.entity_type)
    for name in body.items:
        payload = json.dumps(
            {"job_id": job_id, "entity_type": body.entity_type, "name": name}
        ).encode()
        await js.publish(subject, payload)
    return IngestResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(job_id: str) -> StatusResponse:
    status = await jobs.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return StatusResponse(**status)
```

- [ ] **Step 5: Implement main.py**

Create `apps/v3/langgraph/worker/src/main.py`:

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config import settings
from src.consumer import run_consumer
from src.nats_client import close as nats_close, connect as nats_connect
from src.rate_limiter import TokenBucket
from src.routers import ingest

logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task
    await nats_connect()
    buckets = {
        "npm": TokenBucket(settings.npm_rate_limit_rps),
        "github": TokenBucket(settings.github_rate_limit_rps),
    }
    _consumer_task = asyncio.create_task(run_consumer(buckets))
    logger.info("entity-worker started")
    yield
    if _consumer_task:
        _consumer_task.cancel()
    await nats_close()
    logger.info("entity-worker stopped")


app = FastAPI(title="entity-worker", lifespan=lifespan)
app.include_router(ingest.router)
```

- [ ] **Step 6: Run tests — expect pass**

```bash
uv run pytest tests/unit/test_routes.py -v
```

Expected: 3 tests PASSED.

- [ ] **Step 7: Verify ruff on all src/**

```bash
uv run ruff check src/
```

Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add src/routers/ src/main.py tests/unit/test_routes.py
git commit -m "feat(npm-worker): add FastAPI app with /ingest and /status endpoints"
```

---

### Task 8: Backend — npm cache reader

Work from `apps/v3/langgraph/backend/`.

**Files:**
- Create: `src/services/npm_cache.py`

The existing `src/services/npm_registry_cache.py` will be deleted in Task 11 after both analyze nodes are updated. Do not delete it yet.

- [ ] **Step 1: Write npm_cache.py**

Create `apps/v3/langgraph/backend/src/services/npm_cache.py`:

```python
"""Read-only access to npm_package_cache populated by the npm-worker service."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from src.db.connection import get_db

_COLLECTION = "npm_package_cache"


class NpmPackageCacheEntry(BaseModel):
    name: str
    fetched_at: datetime
    registry_data: dict
    weekly_downloads: int | None = None


async def get_cached(
    name: str, max_age_days: int = 7
) -> NpmPackageCacheEntry | None:
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    doc = await get_db()[_COLLECTION].find_one(
        {"name": name, "fetched_at": {"$gte": cutoff}}
    )
    if not doc:
        return None
    doc.pop("_id", None)
    return NpmPackageCacheEntry(**doc)
```

- [ ] **Step 2: Verify ruff**

```bash
uv run ruff check src/services/npm_cache.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/services/npm_cache.py
git commit -m "feat(backend): add read-only npm cache reader"
```

---

### Task 9: Backend — ingestor HTTP client and config

Work from `apps/v3/langgraph/backend/`.

**Files:**
- Modify: `src/utils/config.py`
- Create: `src/services/npm_ingestor_client.py`

- [ ] **Step 1: Add npm_worker_url to config**

Edit `apps/v3/langgraph/backend/src/utils/config.py` — add the new field inside the `Settings` class after `registry_cache_max_age_days`:

```python
    # npm worker
    npm_worker_url: str = "http://localhost:8001"
```

Full updated file:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # MongoDB
    mongodb_uri: str

    # OpenAI
    openai_api_key: str

    # LangSmith
    langsmith_api_key: str = ""
    langsmith_project: str = "default"

    # GitHub
    github_token: str = ""

    # Docker (runtime subgraph)
    node_docker_image: str = "node:20-slim"
    docker_memory_limit: str = "512m"
    docker_cpu_limit: float = 1.0
    script_timeout_seconds: int = 120

    # Analysis parameters
    lookback_days: int = 90
    reviewer_batch_size: int = 20
    registry_cache_max_age_days: int = 7
    repo_cache_max_age_days: int = 1
    runtime_cache_max_age_days: int = 30

    # npm worker
    npm_worker_url: str = "http://localhost:8001"


settings = Settings()
```

- [ ] **Step 2: Write npm_ingestor_client.py**

Create `apps/v3/langgraph/backend/src/services/npm_ingestor_client.py`:

```python
"""HTTP client for the entity-worker ingestor service."""

import asyncio
import logging

import httpx

from src.utils.config import settings

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5.0
_REQUEST_TIMEOUT = 10.0


async def ingest(entity_type: str, items: list[str]) -> str:
    """Submit items for ingestion. Returns job_id."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.npm_worker_url}/ingest",
            json={"entity_type": entity_type, "items": items},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["job_id"]


async def wait(job_id: str, timeout: float = 300.0) -> None:
    """Poll /status until job is done or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient() as client:
        while asyncio.get_event_loop().time() < deadline:
            resp = await client.get(
                f"{settings.npm_worker_url}/status/{job_id}",
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            if resp.json()["status"] == "done":
                return
            await asyncio.sleep(_POLL_INTERVAL)
    logger.warning("ingestor: timeout waiting for job %s", job_id)
```

- [ ] **Step 3: Verify ruff**

```bash
uv run ruff check src/services/npm_ingestor_client.py src/utils/config.py
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add src/utils/config.py src/services/npm_ingestor_client.py
git commit -m "feat(backend): add npm ingestor HTTP client and config"
```

---

### Task 10: Backend — update supply_chain analyze node

Work from `apps/v3/langgraph/backend/`.

**Files:**
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py`

- [ ] **Step 1: Rewrite the analyze node**

Replace the full content of `src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py`:

```python
"""Supply chain analysis — evaluates npm packages via registry data."""

import logging
from datetime import UTC, datetime

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
from src.services.npm_cache import NpmPackageCacheEntry, get_cached
from src.services.npm_ingestor_client import ingest, wait

logger = logging.getLogger(__name__)

_MAX_PACKAGES = 50
_STALE_DAYS = 730
_VERY_STALE_DAYS = 1825
_LOW_DOWNLOADS = 1_000


def _extract_npm_packages(sbom: dict) -> list[tuple[str, str]]:
    results = []
    for comp in sbom.get("components", []):
        purl = comp.get("purl", "")
        if purl.startswith("pkg:npm/"):
            name = comp.get("name", "")
            version = comp.get("version", "unknown")
            if name:
                results.append((name, version))
    return results[:_MAX_PACKAGES]


def _days_since(iso_str: str) -> int | None:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return (datetime.now(UTC) - dt).days
    except Exception:
        return None


def _build_record(
    name: str,
    version: str,
    entry: NpmPackageCacheEntry | None,
) -> SupplyChainRecord:
    if not entry or not entry.registry_data:
        return SupplyChainRecord(name=name, version=version)

    meta = entry.registry_data
    downloads = entry.weekly_downloads
    flags: list[str] = []
    risk = 0.0

    latest_tag = meta.get("dist-tags", {}).get("latest", version)
    latest_info = meta.get("versions", {}).get(latest_tag, {})

    if latest_info.get("deprecated"):
        flags.append("deprecated")
        risk += 0.5

    time_data = meta.get("time", {})
    last_publish_days: int | None = None
    if latest_tag in time_data:
        last_publish_days = _days_since(time_data[latest_tag])
    elif "modified" in time_data:
        last_publish_days = _days_since(time_data["modified"])

    if last_publish_days is not None:
        if last_publish_days > _VERY_STALE_DAYS:
            flags.append("very-stale")
            risk += 0.35
        elif last_publish_days > _STALE_DAYS:
            flags.append("stale")
            risk += 0.2

    if downloads is not None and downloads < _LOW_DOWNLOADS:
        flags.append("low-downloads")
        risk += 0.2

    maintainers = meta.get("maintainers", [])
    if len(maintainers) == 1:
        flags.append("single-maintainer")
        risk += 0.15

    return SupplyChainRecord(
        name=name,
        version=version,
        risk_score=min(risk, 1.0),
        last_publish_days=last_publish_days,
        weekly_downloads=downloads,
        flags=flags,
    )


async def analyze(state: SupplyChainState) -> dict:
    sbom = state.get("sbom_cyclonedx", {})
    concern = state.get("concern", "")
    packages = _extract_npm_packages(sbom)

    if not packages:
        logger.warning("supply_chain: no npm packages found in SBOM")
        entry = SupplyChainEntry(records=[], high_risk_count=0, concern=concern)
        result_id = await supply_chain_dao.save(entry)
        return {"result_id": result_id}

    names = [name for name, _ in packages]
    job_id = await ingest("npm", names)
    await wait(job_id)

    records = [
        _build_record(name, version, await get_cached(name))
        for name, version in packages
    ]
    entry = SupplyChainEntry(
        records=records,
        high_risk_count=sum(1 for r in records if r.risk_score >= 0.7),
        concern=concern,
    )
    result_id = await supply_chain_dao.save(entry)
    logger.info(
        "supply_chain: %d packages, %d high-risk, result_id=%s",
        len(records),
        entry.high_risk_count,
        result_id,
    )
    return {"result_id": result_id}
```

- [ ] **Step 2: Verify ruff**

```bash
uv run ruff check src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/main_graph/subgraphs/ingestion_subgraphs/supply_chain/nodes/analyze.py
git commit -m "feat(backend): wire supply_chain analyze to npm-worker"
```

---

### Task 11: Backend — update dependency_freshness analyze node and delete npm_registry_cache

Work from `apps/v3/langgraph/backend/`.

**Files:**
- Modify: `src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness/nodes/analyze.py`
- Delete: `src/services/npm_registry_cache.py`

- [ ] **Step 1: Rewrite dependency_freshness analyze node**

Replace the full content of `src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness/nodes/analyze.py`:

```python
"""Dependency freshness — checks npm packages against their latest versions."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.dependency_freshness.dao import (
    dependency_freshness_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.dependency_freshness.models import (
    FreshnessEntry,
    FreshnessRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.dependency_freshness.state import (
    DependencyFreshnessState,
)
from src.services.npm_cache import NpmPackageCacheEntry, get_cached
from src.services.npm_ingestor_client import ingest, wait

logger = logging.getLogger(__name__)

_MAX_PACKAGES = 60


def _parse_version(v: str) -> tuple[int, int, int]:
    cleaned = v.lstrip("v^~>=<")
    parts = cleaned.split(".")
    try:
        major = int(parts[0]) if parts else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2].split("-")[0]) if len(parts) > 2 else 0
        return major, minor, patch
    except (ValueError, IndexError):
        return 0, 0, 0


def _extract_npm_packages(sbom: dict) -> list[tuple[str, str]]:
    results = []
    for comp in sbom.get("components", []):
        purl = comp.get("purl", "")
        if purl.startswith("pkg:npm/"):
            name = comp.get("name", "")
            version = comp.get("version", "unknown")
            if name:
                results.append((name, version))
    return results[:_MAX_PACKAGES]


def _build_record(
    name: str,
    current_version: str,
    entry: NpmPackageCacheEntry | None,
) -> FreshnessRecord:
    if not entry or not entry.registry_data:
        return FreshnessRecord(name=name, current_version=current_version)

    meta = entry.registry_data
    latest = meta.get("dist-tags", {}).get("latest")
    versions = meta.get("versions", {})
    flags: list[str] = []

    current_info = versions.get(current_version, {})
    if current_info.get("deprecated"):
        flags.append("deprecated")

    major_behind = 0
    minor_behind = 0

    if latest and latest != current_version:
        cur = _parse_version(current_version)
        lat = _parse_version(latest)
        if lat > cur:
            major_behind = lat[0] - cur[0]
            if major_behind > 0:
                flags.append("major-outdated")
            elif lat[1] - cur[1] > 0:
                minor_behind = lat[1] - cur[1]
                flags.append("minor-outdated")
            else:
                flags.append("patch-outdated")

    return FreshnessRecord(
        name=name,
        current_version=current_version,
        latest_version=latest,
        major_behind=major_behind,
        minor_behind=minor_behind,
        is_deprecated=bool(current_info.get("deprecated")),
        flags=flags,
    )


async def analyze(state: DependencyFreshnessState) -> dict:
    sbom = state.get("sbom_cyclonedx", {})
    concern = state.get("concern", "")
    packages = _extract_npm_packages(sbom)

    if not packages:
        logger.warning("dependency_freshness: no npm packages in SBOM")
        entry = FreshnessEntry(
            records=[],
            outdated_count=0,
            major_outdated_count=0,
            concern=concern,
        )
        result_id = await dependency_freshness_dao.save(entry)
        return {"result_id": result_id}

    names = [name for name, _ in packages]
    job_id = await ingest("npm", names)
    await wait(job_id)

    records = [
        _build_record(name, version, await get_cached(name))
        for name, version in packages
    ]
    outdated = sum(1 for r in records if r.flags)
    major_outdated = sum(1 for r in records if r.major_behind > 0)

    entry = FreshnessEntry(
        records=records,
        outdated_count=outdated,
        major_outdated_count=major_outdated,
        concern=concern,
    )
    result_id = await dependency_freshness_dao.save(entry)
    logger.info(
        "dependency_freshness: %d packages, %d outdated (%d major), result_id=%s",
        len(records),
        outdated,
        major_outdated,
        result_id,
    )
    return {"result_id": result_id}
```

- [ ] **Step 2: Delete npm_registry_cache.py**

```bash
git rm src/services/npm_registry_cache.py
```

- [ ] **Step 3: Verify ruff on both changed files**

```bash
uv run ruff check \
  src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness/nodes/analyze.py \
  src/services/
```

Expected: no output.

- [ ] **Step 4: Run backend test suite**

```bash
uv run pytest tests/ -v
```

Expected: all existing tests pass (none reference npm_registry_cache directly).

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/subgraphs/ingestion_subgraphs/dependency_freshness/nodes/analyze.py
git commit -m "feat(backend): wire dependency_freshness to npm-worker, remove npm_registry_cache"
```

---

## Running the Full Stack

To test end-to-end after all tasks are complete:

**1. Start NATS with JetStream:**
```bash
docker run -p 4222:4222 nats:latest -js
```

**2. Start the npm-worker** (from `apps/v3/langgraph/worker/`):
```bash
uv run uvicorn src.main:app --port 8001 --reload
```

**3. Start the backend** (from `apps/v3/langgraph/backend/`):
```bash
uv run dev
```

**4. Smoke test the worker directly:**
```bash
curl -s -X POST http://localhost:8001/ingest \
  -H "Content-Type: application/json" \
  -d '{"entity_type": "npm", "items": ["react", "lodash"]}' | jq .

# Copy job_id from above, then:
curl -s http://localhost:8001/status/<job_id> | jq .
```

Expected: status transitions from `pending` → `running` → `done` as packages are fetched.
