# Workers Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate entity-fetch worker logic from `worker/` into the `workers/` hexagonal boilerplate (strict Ports & Adapters + DDD), replacing all boilerplate placeholders, then delete `worker/`.

**Architecture:** Domain layer holds pure models and port ABCs. Adapters implement ports: NATSJetStreamAdapter (messaging), MongoJobRepository + MongoEntityCacheAdapter (persistence), NpmFetcherAdapter + GithubFetcherAdapters (HTTP), RedisRateLimiter (rate limiting). ConsumerService and IngestService orchestrate via injected ports.

**Tech Stack:** Python 3.12, FastAPI, nats-py (JetStream), pymongo (async), redis[hiredis], httpx, pydantic-settings, uv, pytest-asyncio

---

## File Map

**Create:**
- `workers/domain/models/job.py`
- `workers/domain/models/fetch_message.py`
- `workers/domain/models/errors.py`
- `workers/domain/ports/job_repository_port.py`
- `workers/domain/ports/entity_cache_port.py`
- `workers/domain/ports/fetcher_port.py`
- `workers/domain/ports/rate_limit_port.py`
- `workers/adapters/db/mongodb/__init__.py`
- `workers/adapters/db/mongodb/mongo_job_repository.py`
- `workers/adapters/db/mongodb/mongo_entity_cache_adapter.py`
- `workers/adapters/rate_limit/__init__.py`
- `workers/adapters/rate_limit/redis_rate_limiter.py`
- `workers/adapters/fetchers/__init__.py`
- `workers/adapters/fetchers/npm_fetcher_adapter.py`
- `workers/adapters/fetchers/github_fetcher_adapter.py`
- `workers/services/application_services/ingest_service.py`
- `workers/services/application_services/consumer_service.py`
- `workers/api/routers/ingest_router.py`
- `workers/tests/unit/test_fetch_errors.py`
- `workers/tests/unit/test_mongo_job_repository.py`
- `workers/tests/unit/test_redis_rate_limiter.py`
- `workers/tests/unit/test_npm_fetcher_adapter.py`
- `workers/tests/unit/test_github_fetcher_adapter.py`
- `workers/tests/unit/test_consumer_service.py`
- `workers/tests/unit/test_ingest_router.py`

**Modify:**
- `workers/pyproject.toml` — add `[project]` + `[build-system]` sections for uv
- `workers/config/settings.py` — add MongoDB/GitHub/NATS/rate-limit settings, remove DB/AWS/storage settings
- `workers/domain/ports/messaging_port.py` — add JetStream abstract methods
- `workers/adapters/messaging/nats_adapter.py` — full JetStream rewrite
- `workers/api/schemas.py` — replace example schemas with Ingest schemas
- `workers/api/dependencies.py` — rewire all adapters and services
- `workers/main.py` — update lifespan
- `workers/docker-compose.yml` — remove postgres/localstack, add mongodb

**Delete:**
- `workers/adapters/db/sqlalchemy/` (entire dir)
- `workers/adapters/messaging/sns_sqs_adapter.py`
- `workers/adapters/storage/` (entire dir)
- `workers/api/routers/example_router.py`
- `workers/services/application_services/example_service.py`
- `workers/domain/ports/repository_port.py`
- `workers/domain/ports/unit_of_work_port.py`
- `workers/domain/ports/storage_port.py`
- `workers/requirements.txt`
- `workers/requirements-dev.txt`
- `workers/alembic.ini`

---

## Task 1: Project setup — uv, settings, docker-compose

**Files:**
- Modify: `workers/pyproject.toml`
- Modify: `workers/config/settings.py`
- Modify: `workers/docker-compose.yml`

- [ ] **Step 1: Add `[project]` and `[build-system]` to pyproject.toml**

Prepend to the existing `workers/pyproject.toml` (keep all `[tool.*]` sections as-is):

```toml
[project]
name = "entity-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.136.0",
    "uvicorn[standard]>=0.34.0",
    "pydantic-settings>=2.7.0",
    "nats-py>=2.9.0",
    "pymongo>=4.12.0",
    "httpx>=0.28.0",
    "redis[hiredis]>=5.2.0",
    "structlog>=25.0.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.9.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = [
    "adapters", "api", "config", "domain", "services",
]
```

- [ ] **Step 2: Install dependencies with uv**

Run from `workers/`:
```bash
uv sync --dev
```
Expected: creates `.venv/`, resolves packages, no errors.

- [ ] **Step 3: Replace `config/settings.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "entity-worker"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = ["*"]

    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "misi"

    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_DEFAULT_TTL: int = 300

    NATS_URL: str = "nats://localhost:4222"
    NATS_STREAM_NAME: str = "ENTITY_FETCH"
    NATS_SUBJECT_PREFIX: str = "entity.fetch"
    NATS_MAX_DELIVER: int = 5
    NATS_TRANSIENT_BACKOFF_BASE: float = 5.0
    NATS_TRANSIENT_BACKOFF_CAP: float = 300.0

    GITHUB_TOKEN: str = ""
    GITHUB_ISSUES_LOOKBACK_DAYS: int = 30
    GITHUB_RELEASES_LOOKBACK_DAYS: int = 90

    NPM_RATE_WINDOWS: list[tuple[int, int]] = [(60, 500), (3600, 5000)]
    GITHUB_RATE_WINDOWS: list[tuple[int, int]] = [(60, 100), (3600, 5000)]

    WORKER_CONCURRENCY: int = 5
    MAX_RETRIES: int = 3


settings = Settings()
```

- [ ] **Step 4: Replace `docker-compose.yml`**

```yaml
services:
  api:
    build:
      context: .
      dockerfile: Dockerfile.dev
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      MONGODB_URI: mongodb://mongodb:27017
      MONGODB_DB: misi
      REDIS_URL: redis://redis:6379/0
      NATS_URL: nats://nats:4222
    depends_on:
      mongodb:
        condition: service_healthy
      redis:
        condition: service_healthy
      nats:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    networks:
      - app-network

  mongodb:
    image: docker.io/mongo:7
    ports:
      - "27017:27017"
    volumes:
      - mongodb_data:/data/db
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.runCommand('ping').ok", "--quiet"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - app-network

  redis:
    image: docker.io/redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - app-network

  nats:
    image: docker.io/nats:2.10-alpine
    ports:
      - "4222:4222"
      - "8222:8222"
    command: ["--jetstream", "--http_port", "8222"]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8222/healthz || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - app-network

volumes:
  mongodb_data:

networks:
  app-network:
    driver: bridge
```

- [ ] **Step 5: Commit**

```bash
git add workers/pyproject.toml workers/config/settings.py workers/docker-compose.yml
git commit -m "feat(workers): project setup — uv, settings, docker-compose"
```

---

## Task 2: Domain models and errors

**Files:**
- Create: `workers/domain/models/job.py`
- Create: `workers/domain/models/fetch_message.py`
- Create: `workers/domain/models/errors.py`
- Create: `workers/tests/unit/test_fetch_errors.py`

- [ ] **Step 1: Write failing test for errors**

Create `workers/tests/unit/test_fetch_errors.py`:
```python
from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError


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

- [ ] **Step 2: Run to confirm failure**

```bash
cd workers && uv run pytest tests/unit/test_fetch_errors.py -v
```
Expected: `ModuleNotFoundError: No module named 'domain.models.errors'`

- [ ] **Step 3: Create `domain/models/errors.py`**

```python
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

- [ ] **Step 4: Create `domain/models/job.py`**

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Job:
    job_id: str
    packages: list[str]
    total: int
    completed: int
    failed: int
    status: str  # "pending" | "running" | "done"
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 5: Create `domain/models/fetch_message.py`**

```python
from dataclasses import dataclass


@dataclass
class FetchMessage:
    job_id: str
    entity_type: str
    name: str
```

- [ ] **Step 6: Run tests to confirm passing**

```bash
uv run pytest tests/unit/test_fetch_errors.py -v
```
Expected: 3 tests PASSED.

- [ ] **Step 7: Commit**

```bash
git add workers/domain/models/ workers/tests/unit/test_fetch_errors.py
git commit -m "feat(workers): domain models — Job, FetchMessage, errors"
```

---

## Task 3: New domain ports

**Files:**
- Create: `workers/domain/ports/job_repository_port.py`
- Create: `workers/domain/ports/entity_cache_port.py`
- Create: `workers/domain/ports/fetcher_port.py`
- Create: `workers/domain/ports/rate_limit_port.py`

No unit tests needed for ABCs — tested implicitly via adapter tests.

- [ ] **Step 1: Create `domain/ports/job_repository_port.py`**

```python
from abc import ABC, abstractmethod


class JobRepositoryPort(ABC):
    @abstractmethod
    async def create(self, job_id: str, packages: list[str]) -> None: ...

    @abstractmethod
    async def record_success(self, job_id: str) -> None: ...

    @abstractmethod
    async def record_failure(self, job_id: str) -> None: ...

    @abstractmethod
    async def get_status(self, job_id: str) -> dict | None: ...

    @abstractmethod
    async def delete(self, job_id: str) -> None: ...
```

- [ ] **Step 2: Create `domain/ports/entity_cache_port.py`**

```python
from abc import ABC, abstractmethod


class EntityCachePort(ABC):
    @abstractmethod
    async def save(self, collection: str, name: str, doc: dict) -> None: ...

    @abstractmethod
    async def get(self, collection: str, name: str) -> dict | None: ...
```

- [ ] **Step 3: Create `domain/ports/fetcher_port.py`**

```python
from abc import ABC, abstractmethod

import httpx


class FetcherPort(ABC):
    """Concrete classes must define `collection` and `rate_group` as class attributes."""

    collection: str
    rate_group: str

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict: ...
```

- [ ] **Step 4: Create `domain/ports/rate_limit_port.py`**

```python
from abc import ABC, abstractmethod


class RateLimitPort(ABC):
    @abstractmethod
    async def acquire(self, group: str) -> None: ...
```

- [ ] **Step 5: Commit**

```bash
git add workers/domain/ports/
git commit -m "feat(workers): domain ports — JobRepository, EntityCache, Fetcher, RateLimit"
```

---

## Task 4: Upgrade MessagingPort + NATSJetStreamAdapter

**Files:**
- Modify: `workers/domain/ports/messaging_port.py`
- Modify: `workers/adapters/messaging/nats_adapter.py`

- [ ] **Step 1: Add JetStream methods to `domain/ports/messaging_port.py`**

Replace the file contents:
```python
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MessagingPort(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def publish(
        self,
        topic: str,
        message: dict[str, Any],
        attributes: dict[str, str] | None = None,
    ) -> str: ...

    @abstractmethod
    async def subscribe(self, queue: str, handler: MessageHandler) -> None: ...

    @abstractmethod
    async def acknowledge(self, receipt_handle: str) -> None: ...

    # --- JetStream methods ---

    @abstractmethod
    async def add_stream(self, name: str, subjects: list[str]) -> None: ...

    @abstractmethod
    async def pull_subscribe(
        self, stream: str, subject: str, durable: str, max_deliver: int
    ) -> Any: ...

    @abstractmethod
    async def pull_fetch(
        self, subscription: Any, batch: int, timeout: float
    ) -> list[Any]: ...

    @abstractmethod
    async def ack(self, msg: Any) -> None: ...

    @abstractmethod
    async def nak(self, msg: Any, delay: float) -> None: ...

    @abstractmethod
    async def term(self, msg: Any) -> None: ...
```

- [ ] **Step 2: Rewrite `adapters/messaging/nats_adapter.py`**

```python
"""NATS JetStream adapter — full pull-consumer implementation."""

import json
import logging
import uuid
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, RetentionPolicy, StreamConfig
from nats.js.errors import BadRequestError, FetchTimeoutError

from domain.ports.messaging_port import MessageHandler, MessagingPort

logger = logging.getLogger(__name__)


class NATSJetStreamAdapter(MessagingPort):
    def __init__(self, nats_url: str, stream_name: str, subject_prefix: str) -> None:
        self._nats_url = nats_url
        self._stream_name = stream_name
        self._subject_prefix = subject_prefix
        self._client: NATSClient | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> None:
        self._client = await nats.connect(self._nats_url)
        self._js = self._client.jetstream()
        try:
            await self._js.add_stream(
                StreamConfig(
                    name=self._stream_name,
                    subjects=[f"{self._subject_prefix}.*"],
                    retention=RetentionPolicy.WORK_QUEUE,
                )
            )
            logger.info("nats: stream %s created", self._stream_name)
        except BadRequestError:
            logger.debug("nats: stream %s already exists", self._stream_name)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.drain()
            self._client = None
            self._js = None
        logger.info("nats: disconnected")

    async def publish(
        self,
        topic: str,
        message: dict[str, Any],
        attributes: dict[str, str] | None = None,
    ) -> str:
        if self._js is None:
            raise RuntimeError("call connect() before publishing")
        payload = json.dumps(message).encode()
        ack = await self._js.publish(topic, payload)
        return str(ack.seq)

    async def subscribe(self, queue: str, handler: MessageHandler) -> None:
        raise NotImplementedError("use pull_subscribe for JetStream consumers")

    async def acknowledge(self, receipt_handle: str) -> None:
        raise NotImplementedError("use ack(msg) directly")

    async def add_stream(self, name: str, subjects: list[str]) -> None:
        if self._js is None:
            raise RuntimeError("call connect() first")
        try:
            await self._js.add_stream(StreamConfig(name=name, subjects=subjects))
        except BadRequestError:
            pass

    async def pull_subscribe(
        self, stream: str, subject: str, durable: str, max_deliver: int
    ) -> Any:
        if self._js is None:
            raise RuntimeError("call connect() first")
        return await self._js.pull_subscribe(
            subject,
            durable=durable,
            stream=stream,
            config=ConsumerConfig(max_deliver=max_deliver),
        )

    async def pull_fetch(
        self, subscription: Any, batch: int, timeout: float
    ) -> list[Any]:
        try:
            return await subscription.fetch(batch, timeout=timeout)
        except FetchTimeoutError:
            return []

    async def ack(self, msg: Any) -> None:
        await msg.ack()

    async def nak(self, msg: Any, delay: float) -> None:
        await msg.nak(delay=delay)

    async def term(self, msg: Any) -> None:
        await msg.term()
```

- [ ] **Step 3: Verify no import errors**

```bash
uv run python -c "from adapters.messaging.nats_adapter import NATSJetStreamAdapter; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add workers/domain/ports/messaging_port.py workers/adapters/messaging/nats_adapter.py
git commit -m "feat(workers): upgrade MessagingPort + NATSAdapter to JetStream"
```

---

## Task 5: MongoDB adapters

**Files:**
- Create: `workers/adapters/db/mongodb/__init__.py`
- Create: `workers/adapters/db/mongodb/mongo_job_repository.py`
- Create: `workers/adapters/db/mongodb/mongo_entity_cache_adapter.py`
- Create: `workers/tests/unit/test_mongo_job_repository.py`

- [ ] **Step 1: Write failing tests**

Create `workers/tests/unit/test_mongo_job_repository.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from adapters.db.mongodb.mongo_job_repository import MongoJobRepository


def _make_repo():
    mock_col = AsyncMock()
    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)
    return MongoJobRepository(mock_db), mock_col


async def test_create_inserts_correct_document():
    repo, col = _make_repo()
    await repo.create("job-1", ["react", "lodash"])
    col.insert_one.assert_called_once()
    doc = col.insert_one.call_args[0][0]
    assert doc["_id"] == "job-1"
    assert doc["packages"] == ["react", "lodash"]
    assert doc["total"] == 2
    assert doc["completed"] == 0
    assert doc["failed"] == 0
    assert doc["status"] == "pending"
    assert "created_at" in doc
    assert "updated_at" in doc


async def test_record_success_calls_find_one_and_update_and_update_one():
    repo, col = _make_repo()
    await repo.record_success("job-1")
    col.find_one_and_update.assert_called_once()
    col.update_one.assert_called_once()


async def test_record_failure_calls_find_one_and_update_and_update_one():
    repo, col = _make_repo()
    await repo.record_failure("job-1")
    col.find_one_and_update.assert_called_once()
    col.update_one.assert_called_once()


async def test_get_status_returns_none_for_missing():
    repo, col = _make_repo()
    col.find_one = AsyncMock(return_value=None)
    result = await repo.get_status("missing")
    assert result is None


async def test_get_status_returns_dict():
    repo, col = _make_repo()
    col.find_one = AsyncMock(
        return_value={"status": "done", "total": 2, "completed": 2, "failed": 0}
    )
    result = await repo.get_status("job-1")
    assert result == {
        "job_id": "job-1",
        "status": "done",
        "total": 2,
        "completed": 2,
        "failed": 0,
    }


async def test_delete_calls_delete_one():
    repo, col = _make_repo()
    await repo.delete("job-1")
    col.delete_one.assert_called_once_with({"_id": "job-1"})
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_mongo_job_repository.py -v
```
Expected: `ModuleNotFoundError: No module named 'adapters.db.mongodb'`

- [ ] **Step 3: Create `adapters/db/mongodb/__init__.py`** (empty)

- [ ] **Step 4: Create `adapters/db/mongodb/mongo_job_repository.py`**

```python
from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from domain.ports.job_repository_port import JobRepositoryPort


class MongoJobRepository(JobRepositoryPort):
    def __init__(self, db: AsyncDatabase) -> None:
        self._col = db["ingest_jobs"]

    async def create(self, job_id: str, packages: list[str]) -> None:
        now = datetime.now(UTC)
        await self._col.insert_one(
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

    async def record_success(self, job_id: str) -> None:
        await self._record(job_id, "completed")

    async def record_failure(self, job_id: str) -> None:
        await self._record(job_id, "failed")

    async def _record(self, job_id: str, field: str) -> None:
        await self._col.find_one_and_update(
            {"_id": job_id},
            {
                "$inc": {field: 1},
                "$set": {"status": "running", "updated_at": datetime.now(UTC)},
            },
        )
        await self._col.update_one(
            {
                "_id": job_id,
                "$expr": {"$gte": [{"$add": ["$completed", "$failed"]}, "$total"]},
            },
            {"$set": {"status": "done", "updated_at": datetime.now(UTC)}},
        )

    async def get_status(self, job_id: str) -> dict | None:
        doc = await self._col.find_one(
            {"_id": job_id},
            {"_id": 0, "status": 1, "total": 1, "completed": 1, "failed": 1},
        )
        if not doc:
            return None
        return {"job_id": job_id, **doc}

    async def delete(self, job_id: str) -> None:
        await self._col.delete_one({"_id": job_id})
```

- [ ] **Step 5: Create `adapters/db/mongodb/mongo_entity_cache_adapter.py`**

```python
from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from domain.ports.entity_cache_port import EntityCachePort


class MongoEntityCacheAdapter(EntityCachePort):
    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def save(self, collection: str, name: str, doc: dict) -> None:
        await self._db[collection].replace_one(
            {"name": name},
            {"name": name, "fetched_at": datetime.now(UTC), **doc},
            upsert=True,
        )

    async def get(self, collection: str, name: str) -> dict | None:
        return await self._db[collection].find_one({"name": name}, {"_id": 0})
```

- [ ] **Step 6: Run tests to confirm passing**

```bash
uv run pytest tests/unit/test_mongo_job_repository.py -v
```
Expected: 6 tests PASSED.

- [ ] **Step 7: Commit**

```bash
git add workers/adapters/db/mongodb/ workers/tests/unit/test_mongo_job_repository.py
git commit -m "feat(workers): MongoDB adapters — MongoJobRepository, MongoEntityCacheAdapter"
```

---

## Task 6: RedisRateLimiter

**Files:**
- Create: `workers/adapters/rate_limit/__init__.py`
- Create: `workers/adapters/rate_limit/redis_rate_limiter.py`
- Create: `workers/tests/unit/test_redis_rate_limiter.py`

- [ ] **Step 1: Write failing tests**

Create `workers/tests/unit/test_redis_rate_limiter.py`:
```python
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.rate_limit.redis_rate_limiter import RedisRateLimiter


def _make_limiter(windows: dict) -> tuple[RedisRateLimiter, MagicMock]:
    limiter = RedisRateLimiter("redis://unused", windows)
    mock_redis = MagicMock()
    mock_redis.evalsha = AsyncMock()
    mock_redis.script_load = AsyncMock(return_value="faksha")
    mock_redis.zrange = AsyncMock(return_value=[])
    limiter._client = mock_redis
    limiter._sha = "faksha"
    return limiter, mock_redis


async def test_acquire_succeeds_when_slot_available():
    limiter, mock_redis = _make_limiter({"npm": [(60, 100)]})
    mock_redis.evalsha = AsyncMock(return_value=1)
    await limiter.acquire("npm")
    mock_redis.evalsha.assert_called_once()


async def test_acquire_retries_when_no_slot():
    limiter, mock_redis = _make_limiter({"npm": [(60, 100)]})
    mock_redis.evalsha = AsyncMock(side_effect=[0, 1])
    mock_redis.zrange = AsyncMock(return_value=[(b"id", time.time() + 0.05)])

    with patch("adapters.rate_limit.redis_rate_limiter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.acquire("npm")
        mock_sleep.assert_called_once()


async def test_acquire_raises_for_unknown_group():
    limiter, _ = _make_limiter({"npm": [(60, 100)]})
    with pytest.raises(KeyError):
        await limiter.acquire("github")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_redis_rate_limiter.py -v
```
Expected: `ModuleNotFoundError: No module named 'adapters.rate_limit'`

- [ ] **Step 3: Create `adapters/rate_limit/__init__.py`** (empty)

- [ ] **Step 4: Create `adapters/rate_limit/redis_rate_limiter.py`**

```python
"""Redis-backed multi-window sliding rate limiter using a Lua script."""

import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis

from domain.ports.rate_limit_port import RateLimitPort

logger = logging.getLogger(__name__)

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


class RedisRateLimiter(RateLimitPort):
    def __init__(
        self, redis_url: str, windows: dict[str, list[tuple[int, int]]]
    ) -> None:
        """
        windows: maps rate_group -> list of (window_seconds, max_requests).
        Example: {"npm": [(60, 500), (3600, 5000)]}
        """
        self._client: aioredis.Redis = aioredis.from_url(
            redis_url, decode_responses=False
        )
        self._windows = windows
        self._sha: str | None = None
        self._load_lock = asyncio.Lock()

    async def _ensure_loaded(self) -> None:
        async with self._load_lock:
            if self._sha is None:
                self._sha = await self._client.script_load(_LUA)

    async def acquire(self, group: str) -> None:
        """Block until a request slot is available for group."""
        windows = self._windows[group]  # KeyError for unknown group
        await self._ensure_loaded()
        keys = [f"ratelimit:{group}:{w}" for w, _ in windows]
        argv_pairs = [str(v) for w, m in windows for v in (w, m)]

        while True:
            now = time.time()
            req_id = str(uuid.uuid4())
            result = await self._client.evalsha(
                self._sha, len(keys), *keys, str(now), req_id, *argv_pairs
            )
            if result == 1:
                return

            wait = 1.0
            for key, (window_secs, _) in zip(keys, windows):
                oldest = await self._client.zrange(key, 0, 0, withscores=True)
                if oldest:
                    _, score = oldest[0]
                    slot_open = score + window_secs - now
                    wait = max(wait, slot_open)

            logger.debug("rate limiter: %s throttled, waiting %.1fs", group, wait)
            await asyncio.sleep(wait)
```

- [ ] **Step 5: Run tests to confirm passing**

```bash
uv run pytest tests/unit/test_redis_rate_limiter.py -v
```
Expected: 3 tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add workers/adapters/rate_limit/ workers/tests/unit/test_redis_rate_limiter.py
git commit -m "feat(workers): RedisRateLimiter — Lua sliding-window rate limiter"
```

---

## Task 7: npm fetcher adapter

**Files:**
- Create: `workers/adapters/fetchers/__init__.py`
- Create: `workers/adapters/fetchers/npm_fetcher_adapter.py`
- Create: `workers/tests/unit/test_npm_fetcher_adapter.py`

- [ ] **Step 1: Write failing tests**

Create `workers/tests/unit/test_npm_fetcher_adapter.py`:
```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.fetchers.npm_fetcher_adapter import NpmFetcherAdapter
from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.rate_limit_port import RateLimitPort


def _make_fetcher() -> NpmFetcherAdapter:
    mock_limiter = MagicMock(spec=RateLimitPort)
    mock_limiter.acquire = AsyncMock()
    return NpmFetcherAdapter(mock_limiter, max_retries=3)


def _resp(status: int, body: dict | None = None, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body or {})
    r.headers = headers or {}
    return r


async def test_fetch_returns_doc_on_200():
    fetcher = _make_fetcher()
    reg = _resp(200, {"name": "react"})
    dl = _resp(200, {"downloads": 1000})
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[reg, dl])
    doc = await fetcher.fetch(client, "react")
    assert doc["registry_data"] == {"name": "react"}
    assert doc["weekly_downloads"] == 1000


async def test_fetch_raises_rate_limit_on_429():
    fetcher = _make_fetcher()
    r = _resp(429, headers={"Retry-After": "60"})
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    with pytest.raises(RateLimitError) as exc:
        await fetcher.fetch(client, "react")
    assert exc.value.delay == 60.0


async def test_fetch_raises_permanent_on_404():
    fetcher = _make_fetcher()
    r = _resp(404)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    with pytest.raises(PermanentFetchError):
        await fetcher.fetch(client, "no-such-pkg")


async def test_fetch_raises_transient_after_500_exhaustion():
    fetcher = _make_fetcher()
    r = _resp(500)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    with patch("adapters.fetchers.npm_fetcher_adapter.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TransientFetchError):
            await fetcher.fetch(client, "react")
    assert client.get.call_count == 6  # 3 retries × 2 parallel calls


async def test_collection_and_rate_group():
    fetcher = _make_fetcher()
    assert fetcher.collection == "npm_package_cache"
    assert fetcher.rate_group == "npm"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_npm_fetcher_adapter.py -v
```
Expected: `ModuleNotFoundError: No module named 'adapters.fetchers'`

- [ ] **Step 3: Create `adapters/fetchers/__init__.py`** (empty)

- [ ] **Step 4: Create `adapters/fetchers/npm_fetcher_adapter.py`**

```python
"""npm registry metadata fetcher."""

import asyncio
import logging
import urllib.parse

import httpx

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.fetcher_port import FetcherPort
from domain.ports.rate_limit_port import RateLimitPort

logger = logging.getLogger(__name__)

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
_TIMEOUT = 15.0


class NpmFetcherAdapter(FetcherPort):
    collection = "npm_package_cache"
    rate_group = "npm"

    def __init__(self, rate_limiter: RateLimitPort, max_retries: int = 3) -> None:
        self._rate_limiter = rate_limiter
        self._max_retries = max_retries

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict:
        encoded = urllib.parse.quote(name, safe="")
        reg_resp, dl_resp = await asyncio.gather(
            self._get(client, f"{_NPM_REGISTRY}/{encoded}"),
            self._get(client, f"{_NPM_DOWNLOADS}/{encoded}"),
        )
        return {
            "registry_data": reg_resp.json(),
            "weekly_downloads": dl_resp.json().get("downloads"),
        }

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        for attempt in range(self._max_retries):
            await self._rate_limiter.acquire("npm")
            try:
                resp = await client.get(url, timeout=_TIMEOUT)
            except httpx.RequestError as exc:
                if attempt == self._max_retries - 1:
                    raise TransientFetchError(str(exc)) from exc
                await asyncio.sleep(2**attempt)
                continue

            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                delay = float(resp.headers.get("Retry-After", 60))
                raise RateLimitError(delay)
            if resp.status_code == 404:
                raise PermanentFetchError(f"404 for {url}")
            if resp.status_code >= 500:
                if attempt == self._max_retries - 1:
                    raise TransientFetchError(f"status {resp.status_code} for {url}")
                await asyncio.sleep(2**attempt)
                continue
            raise PermanentFetchError(f"unexpected status {resp.status_code} for {url}")

        raise TransientFetchError(f"exhausted {self._max_retries} retries for {url}")
```

- [ ] **Step 5: Run tests to confirm passing**

```bash
uv run pytest tests/unit/test_npm_fetcher_adapter.py -v
```
Expected: 5 tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add workers/adapters/fetchers/ workers/tests/unit/test_npm_fetcher_adapter.py
git commit -m "feat(workers): NpmFetcherAdapter"
```

---

## Task 8: GitHub fetcher adapters

**Files:**
- Modify: `workers/adapters/fetchers/github_fetcher_adapter.py` (create)
- Create: `workers/tests/unit/test_github_fetcher_adapter.py`

- [ ] **Step 1: Write failing tests**

Create `workers/tests/unit/test_github_fetcher_adapter.py`:
```python
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.fetchers.github_fetcher_adapter import (
    GithubAdvisoriesFetcherAdapter,
    GithubIssuesFetcherAdapter,
    GithubReleasesFetcherAdapter,
)
from domain.models.errors import RateLimitError
from domain.ports.rate_limit_port import RateLimitPort


def _make_limiter() -> MagicMock:
    m = MagicMock(spec=RateLimitPort)
    m.acquire = AsyncMock()
    return m


def _resp(status: int, body=None, link: str | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body if body is not None else [])
    h: dict = {}
    if link:
        h["link"] = link
    r.headers = h
    return r


async def test_fetch_issues_returns_list():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30)
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [{"id": 1}]))
    doc = await fetcher.fetch(client, "owner/repo")
    assert doc["issues"] == [{"id": 1}]


async def test_fetch_issues_paginates():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30)
    link = '<https://api.github.com/next>; rel="next"'
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[_resp(200, [{"id": 1}], link=link), _resp(200, [{"id": 2}])]
    )
    doc = await fetcher.fetch(client, "owner/repo")
    assert len(doc["issues"]) == 2


async def test_fetch_issues_raises_rate_limit_on_429():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30, max_retries=1)
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(429, headers={"Retry-After": "30"}))
    with pytest.raises(RateLimitError) as exc:
        await fetcher.fetch(client, "owner/repo")
    assert exc.value.delay == 30.0


async def test_fetch_releases_filters_by_lookback():
    fetcher = GithubReleasesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30)
    now = datetime.now(UTC)
    old = (now - timedelta(days=100)).isoformat()
    recent = (now - timedelta(days=5)).isoformat()
    client = AsyncMock()
    client.get = AsyncMock(
        return_value=_resp(200, [{"id": 1, "published_at": recent}, {"id": 2, "published_at": old}])
    )
    doc = await fetcher.fetch(client, "owner/repo")
    assert len(doc["releases"]) == 1
    assert doc["releases"][0]["id"] == 1


async def test_fetch_advisories_returns_list():
    fetcher = GithubAdvisoriesFetcherAdapter(_make_limiter(), token="tok")
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [{"ghsa_id": "GHSA-1234"}]))
    doc = await fetcher.fetch(client, "owner/repo")
    assert doc["advisories"] == [{"ghsa_id": "GHSA-1234"}]


async def test_collection_and_rate_group_issues():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="", lookback_days=30)
    assert fetcher.collection == "github_issues_cache"
    assert fetcher.rate_group == "github"


async def test_collection_and_rate_group_releases():
    fetcher = GithubReleasesFetcherAdapter(_make_limiter(), token="", lookback_days=90)
    assert fetcher.collection == "github_releases_cache"
    assert fetcher.rate_group == "github"


async def test_collection_and_rate_group_advisories():
    fetcher = GithubAdvisoriesFetcherAdapter(_make_limiter(), token="")
    assert fetcher.collection == "github_advisories_cache"
    assert fetcher.rate_group == "github"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_github_fetcher_adapter.py -v
```
Expected: `ImportError: cannot import name 'GithubIssuesFetcherAdapter'`

- [ ] **Step 3: Create `adapters/fetchers/github_fetcher_adapter.py`**

```python
"""GitHub REST API fetchers: issues, releases, security advisories."""

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.fetcher_port import FetcherPort
from domain.ports.rate_limit_port import RateLimitPort

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"
_TIMEOUT = 20.0


def _next_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None


class _GithubBaseFetcher(FetcherPort):
    rate_group = "github"

    def __init__(
        self, rate_limiter: RateLimitPort, token: str, max_retries: int = 3
    ) -> None:
        self._rate_limiter = rate_limiter
        self._token = token
        self._max_retries = max_retries

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get_pages(self, client: httpx.AsyncClient, url: str) -> list[dict]:
        results: list[dict] = []
        next_url: str | None = url
        while next_url:
            for attempt in range(self._max_retries):
                await self._rate_limiter.acquire("github")
                try:
                    resp = await client.get(
                        next_url, headers=self._auth_headers(), timeout=_TIMEOUT
                    )
                except httpx.RequestError as exc:
                    if attempt == self._max_retries - 1:
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
                    if attempt == self._max_retries - 1:
                        raise TransientFetchError(
                            f"status {resp.status_code} for {next_url}"
                        )
                    continue
                raise PermanentFetchError(
                    f"unexpected status {resp.status_code} for {next_url}"
                )
        return results


class GithubIssuesFetcherAdapter(_GithubBaseFetcher):
    collection = "github_issues_cache"

    def __init__(
        self,
        rate_limiter: RateLimitPort,
        token: str,
        lookback_days: int = 30,
        max_retries: int = 3,
    ) -> None:
        super().__init__(rate_limiter, token, max_retries)
        self._lookback_days = lookback_days

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict:
        since = (
            datetime.now(UTC) - timedelta(days=self._lookback_days)
        ).isoformat()
        url = f"{_BASE}/repos/{name}/issues?state=all&since={since}&per_page=100"
        issues = await self._get_pages(client, url)
        return {"issues": issues}


class GithubReleasesFetcherAdapter(_GithubBaseFetcher):
    collection = "github_releases_cache"

    def __init__(
        self,
        rate_limiter: RateLimitPort,
        token: str,
        lookback_days: int = 90,
        max_retries: int = 3,
    ) -> None:
        super().__init__(rate_limiter, token, max_retries)
        self._lookback_days = lookback_days

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict:
        url = f"{_BASE}/repos/{name}/releases?per_page=100"
        all_releases = await self._get_pages(client, url)
        cutoff = datetime.now(UTC) - timedelta(days=self._lookback_days)
        recent = [
            r
            for r in all_releases
            if r.get("published_at")
            and datetime.fromisoformat(r["published_at"]) >= cutoff
        ]
        return {"releases": recent}


class GithubAdvisoriesFetcherAdapter(_GithubBaseFetcher):
    collection = "github_advisories_cache"

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict:
        url = f"{_BASE}/repos/{name}/security-advisories?per_page=100"
        advisories = await self._get_pages(client, url)
        return {"advisories": advisories}
```

- [ ] **Step 4: Run tests to confirm passing**

```bash
uv run pytest tests/unit/test_github_fetcher_adapter.py -v
```
Expected: 8 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add workers/adapters/fetchers/github_fetcher_adapter.py workers/tests/unit/test_github_fetcher_adapter.py
git commit -m "feat(workers): GitHub fetcher adapters — issues, releases, advisories"
```

---

## Task 9: IngestService

**Files:**
- Create: `workers/services/application_services/ingest_service.py`
- (No separate test file — tested via router test in Task 11)

- [ ] **Step 1: Create `services/application_services/ingest_service.py`**

```python
import json
import logging
import uuid

from domain.ports.job_repository_port import JobRepositoryPort
from domain.ports.messaging_port import MessagingPort

logger = logging.getLogger(__name__)


class IngestService:
    def __init__(
        self,
        job_repo: JobRepositoryPort,
        messaging: MessagingPort,
        subject_prefix: str,
    ) -> None:
        self._job_repo = job_repo
        self._messaging = messaging
        self._subject_prefix = subject_prefix

    async def create_job(self, entity_type: str, items: list[str]) -> str:
        job_id = str(uuid.uuid4())
        await self._job_repo.create(job_id, items)
        subject = f"{self._subject_prefix}.{entity_type}"
        try:
            for name in items:
                await self._messaging.publish(
                    subject,
                    {"job_id": job_id, "entity_type": entity_type, "name": name},
                )
        except Exception:
            await self._job_repo.delete(job_id)
            raise
        logger.info("ingest: created job %s (%d items)", job_id, len(items))
        return job_id

    async def get_status(self, job_id: str) -> dict | None:
        return await self._job_repo.get_status(job_id)
```

- [ ] **Step 2: Verify import**

```bash
uv run python -c "from services.application_services.ingest_service import IngestService; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add workers/services/application_services/ingest_service.py
git commit -m "feat(workers): IngestService — create job, publish to NATS"
```

---

## Task 10: ConsumerService

**Files:**
- Create: `workers/services/application_services/consumer_service.py`
- Create: `workers/tests/unit/test_consumer_service.py`

- [ ] **Step 1: Write failing tests**

Create `workers/tests/unit/test_consumer_service.py`:
```python
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.entity_cache_port import EntityCachePort
from domain.ports.fetcher_port import FetcherPort
from domain.ports.job_repository_port import JobRepositoryPort
from domain.ports.messaging_port import MessagingPort
from services.application_services.consumer_service import ConsumerService


def _make_settings():
    s = MagicMock()
    s.NATS_MAX_DELIVER = 5
    s.NATS_TRANSIENT_BACKOFF_BASE = 5.0
    s.NATS_TRANSIENT_BACKOFF_CAP = 300.0
    s.WORKER_CONCURRENCY = 1
    s.NATS_STREAM_NAME = "ENTITY_FETCH"
    s.NATS_SUBJECT_PREFIX = "entity.fetch"
    return s


def _make_msg(entity_type: str = "npm", num_delivered: int = 1) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(
        {"job_id": "j1", "entity_type": entity_type, "name": "react"}
    ).encode()
    msg.metadata = MagicMock()
    msg.metadata.num_delivered = num_delivered
    return msg


def _make_service():
    mock_messaging = MagicMock(spec=MessagingPort)
    mock_messaging.ack = AsyncMock()
    mock_messaging.nak = AsyncMock()
    mock_messaging.term = AsyncMock()

    mock_fetcher = MagicMock(spec=FetcherPort)
    mock_fetcher.fetch = AsyncMock(return_value={"data": {}})
    mock_fetcher.collection = "npm_package_cache"

    mock_entity_cache = MagicMock(spec=EntityCachePort)
    mock_entity_cache.save = AsyncMock()

    mock_job_repo = MagicMock(spec=JobRepositoryPort)
    mock_job_repo.record_success = AsyncMock()
    mock_job_repo.record_failure = AsyncMock()

    service = ConsumerService(
        messaging=mock_messaging,
        fetcher_registry={"npm": mock_fetcher},
        entity_cache=mock_entity_cache,
        job_repo=mock_job_repo,
        settings=_make_settings(),
    )
    return service, mock_messaging, mock_fetcher, mock_entity_cache, mock_job_repo


async def test_process_success_acks_and_records_success():
    service, messaging, fetcher, entity_cache, job_repo = _make_service()
    await service._process(_make_msg(), AsyncMock())
    messaging.ack.assert_called_once()
    job_repo.record_success.assert_called_once_with("j1")
    messaging.nak.assert_not_called()
    messaging.term.assert_not_called()


async def test_process_rate_limit_naks_with_delay():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=RateLimitError(45.0))
    await service._process(_make_msg(), AsyncMock())
    messaging.nak.assert_called_once_with(_make_msg(), delay=45.0)
    job_repo.record_failure.assert_not_called()


async def test_process_permanent_error_terms_and_records_failure():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=PermanentFetchError("404"))
    await service._process(_make_msg(), AsyncMock())
    messaging.term.assert_called_once()
    job_repo.record_failure.assert_called_once_with("j1")


async def test_process_transient_naks_when_retries_remain():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=TransientFetchError("timeout"))
    await service._process(_make_msg(num_delivered=2), AsyncMock())
    messaging.nak.assert_called_once()
    nak_kwargs = messaging.nak.call_args[1]
    assert nak_kwargs["delay"] > 0
    job_repo.record_failure.assert_not_called()


async def test_process_transient_terms_when_max_deliver_exhausted():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=TransientFetchError("timeout"))
    await service._process(_make_msg(num_delivered=5), AsyncMock())
    messaging.term.assert_called_once()
    job_repo.record_failure.assert_called_once_with("j1")


async def test_process_unexpected_error_terms_and_records_failure():
    service, messaging, fetcher, _, job_repo = _make_service()
    fetcher.fetch = AsyncMock(side_effect=RuntimeError("unexpected"))
    await service._process(_make_msg(), AsyncMock())
    messaging.term.assert_called_once()
    job_repo.record_failure.assert_called_once_with("j1")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_consumer_service.py -v
```
Expected: `ModuleNotFoundError: No module named 'services.application_services.consumer_service'`

- [ ] **Step 3: Create `services/application_services/consumer_service.py`**

```python
import asyncio
import json
import logging
from typing import Any

import httpx

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.entity_cache_port import EntityCachePort
from domain.ports.fetcher_port import FetcherPort
from domain.ports.job_repository_port import JobRepositoryPort
from domain.ports.messaging_port import MessagingPort

logger = logging.getLogger(__name__)


class ConsumerService:
    def __init__(
        self,
        messaging: MessagingPort,
        fetcher_registry: dict[str, FetcherPort],
        entity_cache: EntityCachePort,
        job_repo: JobRepositoryPort,
        settings: Any,
    ) -> None:
        self._messaging = messaging
        self._fetcher_registry = fetcher_registry
        self._entity_cache = entity_cache
        self._job_repo = job_repo
        self._settings = settings

    async def run(self) -> None:
        sub = await self._messaging.pull_subscribe(
            stream=self._settings.NATS_STREAM_NAME,
            subject=f"{self._settings.NATS_SUBJECT_PREFIX}.*",
            durable="entity-worker",
            max_deliver=self._settings.NATS_MAX_DELIVER,
        )
        async with httpx.AsyncClient() as client:
            tasks = [
                asyncio.create_task(self._worker(sub, client))
                for _ in range(self._settings.WORKER_CONCURRENCY)
            ]
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    async def _worker(self, sub: Any, client: httpx.AsyncClient) -> None:
        while True:
            try:
                msgs = await self._messaging.pull_fetch(sub, 1, timeout=1.0)
                for msg in msgs:
                    await self._process(msg, client)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("worker: fetch error", exc_info=True)
                await asyncio.sleep(0.1)

    def _backoff(self, num_delivered: int) -> float:
        delay = self._settings.NATS_TRANSIENT_BACKOFF_BASE * (
            2 ** (num_delivered - 1)
        )
        return min(delay, self._settings.NATS_TRANSIENT_BACKOFF_CAP)

    async def _process(self, msg: Any, client: httpx.AsyncClient) -> None:
        data = json.loads(msg.data)
        job_id: str = data["job_id"]
        entity_type: str = data["entity_type"]
        name: str = data["name"]
        num_delivered: int = msg.metadata.num_delivered

        try:
            fetcher = self._fetcher_registry[entity_type]
            doc = await fetcher.fetch(client, name)
            await self._entity_cache.save(fetcher.collection, name, doc)
            await self._job_repo.record_success(job_id)
            await self._messaging.ack(msg)

        except RateLimitError as exc:
            logger.warning("consumer: rate limited %s/%s, requeue in %.0fs", entity_type, name, exc.delay)
            await self._messaging.nak(msg, delay=exc.delay)

        except TransientFetchError as exc:
            if num_delivered >= self._settings.NATS_MAX_DELIVER:
                logger.error("consumer: exhausted retries %s/%s: %s", entity_type, name, exc)
                await self._messaging.term(msg)
                await self._job_repo.record_failure(job_id)
            else:
                delay = self._backoff(num_delivered)
                logger.warning("consumer: transient %s/%s, requeue in %.0fs", entity_type, name, delay)
                await self._messaging.nak(msg, delay=delay)

        except PermanentFetchError as exc:
            logger.error("consumer: permanent error %s/%s: %s", entity_type, name, exc)
            await self._messaging.term(msg)
            await self._job_repo.record_failure(job_id)

        except Exception as exc:
            logger.error("consumer: unexpected error %s/%s: %s", entity_type, name, exc)
            await self._messaging.term(msg)
            await self._job_repo.record_failure(job_id)
```

- [ ] **Step 4: Run tests to confirm passing**

```bash
uv run pytest tests/unit/test_consumer_service.py -v
```
Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add workers/services/application_services/consumer_service.py workers/tests/unit/test_consumer_service.py
git commit -m "feat(workers): ConsumerService — NATS pull-consumer with ack/nak/term"
```

---

## Task 11: API layer — schemas, router, dependencies

**Files:**
- Modify: `workers/api/schemas.py`
- Create: `workers/api/routers/ingest_router.py`
- Modify: `workers/api/dependencies.py`
- Create: `workers/tests/unit/test_ingest_router.py`

- [ ] **Step 1: Write failing router tests**

Create `workers/tests/unit/test_ingest_router.py`:
```python
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock

from api.routers.ingest_router import get_status, ingest
from api.schemas import IngestRequest, StatusResponse
from domain.ports.fetcher_port import FetcherPort


def _make_registry() -> dict[str, FetcherPort]:
    return {"npm": MagicMock(), "github_issues": MagicMock()}


async def test_ingest_returns_job_id():
    mock_service = MagicMock()
    mock_service.create_job = AsyncMock(return_value="job-123")
    req = IngestRequest(entity_type="npm", items=["react", "lodash"])
    result = await ingest(req, mock_service, _make_registry())
    assert result.job_id == "job-123"
    mock_service.create_job.assert_called_once_with("npm", ["react", "lodash"])


async def test_ingest_rejects_unknown_entity_type():
    mock_service = MagicMock()
    req = IngestRequest(entity_type="unknown", items=["foo"])
    with pytest.raises(HTTPException) as exc:
        await ingest(req, mock_service, _make_registry())
    assert exc.value.status_code == 422


async def test_ingest_raises_503_on_publish_failure():
    mock_service = MagicMock()
    mock_service.create_job = AsyncMock(side_effect=Exception("nats down"))
    req = IngestRequest(entity_type="npm", items=["react"])
    with pytest.raises(HTTPException) as exc:
        await ingest(req, mock_service, _make_registry())
    assert exc.value.status_code == 503


async def test_status_returns_progress():
    mock_service = MagicMock()
    mock_service.get_status = AsyncMock(
        return_value={
            "job_id": "job-1", "status": "running",
            "total": 10, "completed": 5, "failed": 0,
        }
    )
    result = await get_status("job-1", mock_service)
    assert result.completed == 5


async def test_status_returns_404_for_missing_job():
    mock_service = MagicMock()
    mock_service.get_status = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await get_status("missing", mock_service)
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_ingest_router.py -v
```
Expected: `ImportError: cannot import name 'get_status' from 'api.routers.ingest_router'`

- [ ] **Step 3: Replace `api/schemas.py`**

```python
from typing import Annotated

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    entity_type: str
    items: Annotated[list[str], Field(min_length=1)]


class IngestResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    failed: int
```

- [ ] **Step 4: Create `api/routers/ingest_router.py`**

```python
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_fetcher_registry, get_ingest_service
from api.schemas import IngestRequest, IngestResponse, StatusResponse
from domain.ports.fetcher_port import FetcherPort
from services.application_services.ingest_service import IngestService

router = APIRouter(tags=["ingest"])


@router.post("/ingest", status_code=201, response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    ingest_service: IngestService = Depends(get_ingest_service),
    registry: dict[str, FetcherPort] = Depends(get_fetcher_registry),
) -> IngestResponse:
    if body.entity_type not in registry:
        raise HTTPException(
            status_code=422,
            detail=f"unknown entity_type {body.entity_type!r}, must be one of {sorted(registry)}",
        )
    try:
        job_id = await ingest_service.create_job(body.entity_type, body.items)
    except Exception:
        raise HTTPException(status_code=503, detail="failed to enqueue job")
    return IngestResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(
    job_id: str,
    ingest_service: IngestService = Depends(get_ingest_service),
) -> StatusResponse:
    status = await ingest_service.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return StatusResponse(**status)
```

- [ ] **Step 5: Rewrite `api/dependencies.py`**

```python
"""FastAPI dependency injection wiring — all adapters and services."""

from pymongo import AsyncMongoClient

from adapters.cache.redis_adapter import RedisCacheAdapter
from adapters.db.mongodb.mongo_entity_cache_adapter import MongoEntityCacheAdapter
from adapters.db.mongodb.mongo_job_repository import MongoJobRepository
from adapters.fetchers.github_fetcher_adapter import (
    GithubAdvisoriesFetcherAdapter,
    GithubIssuesFetcherAdapter,
    GithubReleasesFetcherAdapter,
)
from adapters.fetchers.npm_fetcher_adapter import NpmFetcherAdapter
from adapters.messaging.nats_adapter import NATSJetStreamAdapter
from adapters.rate_limit.redis_rate_limiter import RedisRateLimiter
from config.settings import settings
from domain.ports.fetcher_port import FetcherPort
from domain.ports.messaging_port import MessagingPort
from services.application_services.consumer_service import ConsumerService
from services.application_services.ingest_service import IngestService

# ---------------------------------------------------------------------------
# Infrastructure clients
# ---------------------------------------------------------------------------

_mongo_client = AsyncMongoClient(settings.MONGODB_URI)
_mongo_db = _mongo_client[settings.MONGODB_DB]

# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

_job_repo = MongoJobRepository(_mongo_db)
_entity_cache = MongoEntityCacheAdapter(_mongo_db)

_messaging = NATSJetStreamAdapter(
    nats_url=settings.NATS_URL,
    stream_name=settings.NATS_STREAM_NAME,
    subject_prefix=settings.NATS_SUBJECT_PREFIX,
)

_cache = RedisCacheAdapter(settings.REDIS_URL)

_rate_limiter = RedisRateLimiter(
    redis_url=settings.REDIS_URL,
    windows={
        "npm": settings.NPM_RATE_WINDOWS,
        "github": settings.GITHUB_RATE_WINDOWS,
    },
)

_fetcher_registry: dict[str, FetcherPort] = {
    "npm": NpmFetcherAdapter(_rate_limiter, settings.MAX_RETRIES),
    "github_issues": GithubIssuesFetcherAdapter(
        _rate_limiter,
        settings.GITHUB_TOKEN,
        settings.GITHUB_ISSUES_LOOKBACK_DAYS,
        settings.MAX_RETRIES,
    ),
    "github_releases": GithubReleasesFetcherAdapter(
        _rate_limiter,
        settings.GITHUB_TOKEN,
        settings.GITHUB_RELEASES_LOOKBACK_DAYS,
        settings.MAX_RETRIES,
    ),
    "github_advisories": GithubAdvisoriesFetcherAdapter(
        _rate_limiter,
        settings.GITHUB_TOKEN,
        settings.MAX_RETRIES,
    ),
}

# ---------------------------------------------------------------------------
# Application services
# ---------------------------------------------------------------------------

_ingest_service = IngestService(
    job_repo=_job_repo,
    messaging=_messaging,
    subject_prefix=settings.NATS_SUBJECT_PREFIX,
)

_consumer_service = ConsumerService(
    messaging=_messaging,
    fetcher_registry=_fetcher_registry,
    entity_cache=_entity_cache,
    job_repo=_job_repo,
    settings=settings,
)

# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------


def get_messaging() -> MessagingPort:
    return _messaging


def get_fetcher_registry() -> dict[str, FetcherPort]:
    return _fetcher_registry


def get_ingest_service() -> IngestService:
    return _ingest_service


def get_consumer_service() -> ConsumerService:
    return _consumer_service


async def shutdown() -> None:
    await _cache.close()
    _mongo_client.close()
```

- [ ] **Step 6: Run router tests to confirm passing**

```bash
uv run pytest tests/unit/test_ingest_router.py -v
```
Expected: 5 tests PASSED.

- [ ] **Step 7: Commit**

```bash
git add workers/api/schemas.py workers/api/routers/ingest_router.py workers/api/dependencies.py workers/tests/unit/test_ingest_router.py
git commit -m "feat(workers): API layer — IngestRequest/Response schemas, ingest router, DI wiring"
```

---

## Task 12: Update main.py lifespan

**Files:**
- Modify: `workers/main.py`

- [ ] **Step 1: Replace `main.py`**

```python
"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.dependencies import get_consumer_service, get_messaging, shutdown
from api.routers import ingest_router
from config.settings import settings

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _consumer_task
    logger.info("starting %s v%s", settings.APP_NAME, settings.APP_VERSION)

    await get_messaging().connect()
    _consumer_task = asyncio.create_task(get_consumer_service().run())

    yield

    if _consumer_task:
        _consumer_task.cancel()
        await asyncio.gather(_consumer_task, return_exceptions=True)
    await get_messaging().disconnect()
    await shutdown()
    logger.info("stopped %s", settings.APP_NAME)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(ingest_router.router, prefix=settings.API_PREFIX)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
```

- [ ] **Step 2: Verify the app imports cleanly**

```bash
uv run python -c "from main import app; print('ok')"
```
Expected: `ok` (no import errors; NATS/MongoDB/Redis connect only in lifespan, not at import time)

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest tests/unit/ -v
```
Expected: all tests PASSED.

- [ ] **Step 4: Commit**

```bash
git add workers/main.py
git commit -m "feat(workers): update main.py lifespan — connect NATS, start ConsumerService"
```

---

## Task 13: Delete boilerplate files

**Files:** all boilerplate removed here.

- [ ] **Step 1: Delete SQLAlchemy adapter directory**

```bash
rm -rf workers/adapters/db/sqlalchemy
rm -f workers/alembic.ini
```

- [ ] **Step 2: Delete SNS/SQS adapter**

```bash
rm -f workers/adapters/messaging/sns_sqs_adapter.py
```

- [ ] **Step 3: Delete storage adapter**

```bash
rm -rf workers/adapters/storage
```

- [ ] **Step 4: Delete example router and service**

```bash
rm -f workers/api/routers/example_router.py
rm -f workers/services/application_services/example_service.py
```

- [ ] **Step 5: Delete obsolete domain ports**

```bash
rm -f workers/domain/ports/repository_port.py
rm -f workers/domain/ports/unit_of_work_port.py
rm -f workers/domain/ports/storage_port.py
```

- [ ] **Step 6: Delete requirements files**

```bash
rm -f workers/requirements.txt workers/requirements-dev.txt
```

- [ ] **Step 7: Run full test suite to confirm nothing broken**

```bash
cd workers && uv run pytest tests/unit/ -v
```
Expected: all tests still PASSED.

- [ ] **Step 8: Delete old worker directory**

```bash
rm -rf worker/
```

- [ ] **Step 9: Final test run**

```bash
cd workers && uv run pytest tests/unit/ -v
```
Expected: all tests PASSED.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "chore: remove boilerplate adapters/example code and delete worker/ directory"
```

---

## Self-Review Checklist

- [x] **Spec §3 Domain models** → Task 2
- [x] **Spec §3 Ports** → Task 3
- [x] **Spec §4.1 NATSJetStreamAdapter** → Task 4
- [x] **Spec §4.2 MongoJobRepository + MongoEntityCacheAdapter** → Task 5
- [x] **Spec §4.3 NpmFetcherAdapter** → Task 7
- [x] **Spec §4.3 GithubFetcherAdapters** → Task 8
- [x] **Spec §4.4 RedisRateLimiter** → Task 6
- [x] **Spec §4.5 Delete boilerplate adapters** → Task 13
- [x] **Spec §5 IngestService** → Task 9
- [x] **Spec §5 ConsumerService** → Task 10
- [x] **Spec §6 API layer** → Task 11
- [x] **Spec §7 Settings** → Task 1
- [x] **Spec §8 main.py lifespan** → Task 12
- [x] **Spec §9 docker-compose** → Task 1
- [x] **Spec §9 uv** → Task 1
- [x] **Spec §10 Error handling** → Task 10 (ConsumerService._process)
- [x] **Spec §11 Tests** → Tasks 2, 5, 6, 7, 8, 9, 10, 11
- [x] **Spec §12 Delete worker/** → Task 13
