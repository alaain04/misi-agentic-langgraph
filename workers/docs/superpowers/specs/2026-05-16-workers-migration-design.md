# Workers Migration Design

**Date:** 2026-05-16
**Status:** Approved
**Scope:** Migrate `worker/` logic into `workers/` hexagonal boilerplate

---

## 1. Goal

Migrate the entity-fetch worker (NATS consumer, npm/GitHub fetchers, job tracking) from the flat `worker/` layout into the `workers/` hexagonal (Ports & Adapters + DDD) boilerplate, then delete `worker/`.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                            │
│  POST /ingest   GET /status/{job_id}                        │
│  api/routers/ingest_router.py · api/schemas.py              │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│              Application Services Layer                     │
│  IngestService   (create job, publish messages to NATS)     │
│  ConsumerService (process msg, call fetcher, update job)    │
└──────────────────────┬──────────────────────────────────────┘
                       │ uses ports
┌──────────────────────▼──────────────────────────────────────┐
│                     Domain Layer                            │
│  Models: Job, FetchMessage                                  │
│  Ports:  MessagingPort · CachePort · JobRepositoryPort      │
│          EntityCachePort · FetcherPort · RateLimitPort      │
└──────────────────────┬──────────────────────────────────────┘
                       │ implemented by
┌──────────────────────▼──────────────────────────────────────┐
│                    Adapters Layer                           │
│  NATSJetStreamAdapter    RedisCacheAdapter (existing)       │
│  MongoJobRepository      MongoEntityCacheAdapter            │
│  NpmFetcherAdapter       GithubFetcherAdapter (×3 subtypes) │
│  RedisRateLimiter        (Lua script lives here)            │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│               Infrastructure / Config                       │
│  config/settings.py · .env · docker-compose.yml             │
│  MongoDB · Redis · NATS JetStream                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Domain Layer

### 3.1 Models (`domain/models/`)

**`job.py`**
```python
@dataclass
class Job:
    job_id: str
    packages: list[str]
    total: int
    completed: int
    failed: int
    status: str          # "pending" | "running" | "done"
    created_at: datetime
    updated_at: datetime
```

**`fetch_message.py`**
```python
@dataclass
class FetchMessage:
    job_id: str
    entity_type: str
    name: str
```

### 3.2 Ports (`domain/ports/`)

| Port | File | Contract |
|------|------|----------|
| `MessagingPort` | existing (upgraded) | `connect/disconnect/publish/subscribe/acknowledge` |
| `CachePort` | existing | `get/set/delete/exists/flush` |
| `JobRepositoryPort` | `job_repository_port.py` | `create / record_success / record_failure / get_status / delete` |
| `EntityCachePort` | `entity_cache_port.py` | `save(collection, name, doc) / get(collection, name)` |
| `FetcherPort` | `fetcher_port.py` | `fetch(client, name) -> dict` + `collection: str` + `rate_group: str` |
| `RateLimitPort` | `rate_limit_port.py` | `acquire(group: str) -> None` |

**`MessagingPort` additions for JetStream:**
```python
async def pull_subscribe(stream: str, subject: str, durable: str, max_deliver: int) -> Any
async def pull_fetch(subscription: Any, batch: int, timeout: float) -> list[Any]
async def ack(msg: Any) -> None
async def nak(msg: Any, delay: float) -> None
async def term(msg: Any) -> None
async def add_stream(name: str, subjects: list[str]) -> None
```

---

## 4. Adapters Layer

### 4.1 `adapters/messaging/nats_adapter.py` — upgrade to JetStream

Replace the existing NATS Core adapter with a full JetStream implementation:
- `connect()`: connects and creates the `ENTITY_FETCH` stream with work-queue retention if it doesn't exist
- `pull_subscribe()`: creates a durable pull consumer
- `pull_fetch()` / `ack()` / `nak()` / `term()`: JetStream message lifecycle
- `publish()`: JetStream publish (returns message ID from ack)
- Keep `disconnect()` draining the connection

### 4.2 `adapters/db/mongodb/` — new

**`mongo_job_repository.py`** — implements `JobRepositoryPort`
- Uses `pymongo` async client
- Collection: `ingest_jobs`
- Methods mirror existing `worker/src/jobs.py` logic exactly

**`mongo_entity_cache_adapter.py`** — implements `EntityCachePort`
- `save(collection, name, doc)`: upsert by `name`, add `fetched_at`
- `get(collection, name)`: find by name

### 4.3 `adapters/fetchers/` — new

**`npm_fetcher_adapter.py`** — implements `FetcherPort`
- `collection = "npm_package_cache"`, `rate_group = "npm"`
- Constructor receives `RateLimitPort` — used inside `fetch(client, name)`
- Logic: copy from `worker/src/fetchers/npm.py`

**`github_fetcher_adapter.py`** — implements `FetcherPort`
- Three concrete classes: `GithubIssuesFetcherAdapter`, `GithubReleasesFetcherAdapter`, `GithubAdvisoriesFetcherAdapter`
- Collections: `github_issues_cache`, `github_releases_cache`, `github_advisories_cache`
- `rate_group = "github"` for all three
- Each constructor receives `RateLimitPort` and settings (token, lookback days)
- Logic: copy from `worker/src/fetchers/github.py`

### 4.4 `adapters/rate_limit/redis_rate_limiter.py` — new

Implements `RateLimitPort`. Contains the Lua sliding-window script from `worker/src/rate_limiter.py`. Constructor takes `windows: dict[str, list[tuple[int, int]]]` and a Redis client.

### 4.5 Removed from boilerplate

- `adapters/db/sqlalchemy/` (entire directory)
- `adapters/messaging/sns_sqs_adapter.py`
- `adapters/storage/`

---

## 5. Application Services Layer

### `services/application_services/ingest_service.py`

```python
class IngestService:
    def __init__(self, job_repo: JobRepositoryPort, messaging: MessagingPort): ...

    async def create_job(self, entity_type: str, items: list[str]) -> str:
        # creates job in mongo, publishes one FetchMessage per item to NATS
        # on publish failure: deletes job and raises

    async def get_status(self, job_id: str) -> dict | None:
        # delegates to job_repo
```

### `services/application_services/consumer_service.py`

```python
class ConsumerService:
    def __init__(
        self,
        messaging: MessagingPort,
        fetcher_registry: dict[str, FetcherPort],
        entity_cache: EntityCachePort,
        job_repo: JobRepositoryPort,
        rate_limiter: RateLimitPort,
        settings: Settings,
    ): ...

    async def run(self) -> None:
        # sets up pull subscription, spawns N worker tasks

    async def _worker(self, sub, client: httpx.AsyncClient) -> None:
        # pull-fetch loop; calls _process per message

    async def _process(self, msg, client: httpx.AsyncClient) -> None:
        # dispatch by entity_type, save result, ack/nak/term
        # mirrors existing consumer.py _process() logic
```

**Backoff:** exponential, `base * 2^(num_delivered-1)`, capped — same as existing.

---

## 6. API Layer

### `api/routers/ingest_router.py`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ingest` | Validate entity_type, create job, publish |
| `GET` | `/status/{job_id}` | Return job progress |

Schema validation: `entity_type` must be in the fetcher registry (model_validator, same pattern as existing).

### `api/schemas.py`

- `IngestRequest(entity_type, items)`
- `IngestResponse(job_id)`
- `StatusResponse(job_id, status, total, completed, failed)`

### `api/dependencies.py`

Wire all adapters and services:
- Remove DB session, storage
- Add `get_mongo_client()`, `get_job_repo()`, `get_entity_cache()`, `get_rate_limiter()`, `get_fetcher_registry()`, `get_consumer_service()`, `get_ingest_service()`

---

## 7. Config (`config/settings.py`)

Add to existing settings (keep all app/api/redis/nats settings):

```python
MONGODB_URI: str = "mongodb://localhost:27017"
MONGODB_DB: str = "misi"

GITHUB_TOKEN: str = ""
GITHUB_ISSUES_LOOKBACK_DAYS: int = 30
GITHUB_RELEASES_LOOKBACK_DAYS: int = 90

NPM_RATE_WINDOWS: list[tuple[int, int]] = [(60, 500), (3600, 5000)]
GITHUB_RATE_WINDOWS: list[tuple[int, int]] = [(60, 100), (3600, 5000)]

WORKER_CONCURRENCY: int = 5
MAX_RETRIES: int = 3
NATS_STREAM_NAME: str = "ENTITY_FETCH"
NATS_SUBJECT_PREFIX: str = "entity.fetch"
NATS_MAX_DELIVER: int = 5
NATS_TRANSIENT_BACKOFF_BASE: float = 5.0
NATS_TRANSIENT_BACKOFF_CAP: float = 300.0
```

Remove: `DB_*`, `DATABASE_*`, `AWS_*`, `SNS_*`, `SQS_*`, `STORAGE_*`, `S3_*`, `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `ALGORITHM`.

---

## 8. `main.py` lifespan

```
startup:
  connect NATS (NATSJetStreamAdapter)
  connect Redis (RedisCacheAdapter)
  connect MongoDB (MongoClient)
  start ConsumerService.run() as background task

shutdown:
  cancel consumer task
  disconnect NATS
  close Redis
  close MongoDB
```

---

## 9. Infrastructure

### `docker-compose.yml`

Remove: `postgres`, `localstack`. Keep: `api`, `redis`, `nats`. Add: `mongodb` (`mongo:7`).

### Package manager

Convert from `requirements.txt` to `uv`:
- Add `[project]` section to `pyproject.toml`
- Remove `requirements.txt` and `requirements-dev.txt`
- Dependencies: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `nats-py`, `pymongo`, `httpx`, `redis[hiredis]`, `structlog`

---

## 10. Error Handling

| Error type | Action |
|------------|--------|
| `RateLimitError` | `nak(delay=exc.delay)` |
| `TransientFetchError` (retries remaining) | `nak(delay=backoff)` |
| `TransientFetchError` (retries exhausted) | `term()` + `job_repo.record_failure()` |
| `PermanentFetchError` | `term()` + `job_repo.record_failure()` |
| Unexpected exception | `term()` + `job_repo.record_failure()` |

Error types live in `domain/models/errors.py` (moved from `fetchers/errors.py`).

---

## 11. Tests

Existing tests in `worker/tests/unit/` are migrated and updated for the new import paths:

| Existing test file | Migrated to |
|-------------------|-------------|
| `test_jobs.py` | `tests/unit/test_mongo_job_repository.py` |
| `test_rate_limiter.py` | `tests/unit/test_redis_rate_limiter.py` |
| `test_npm_fetcher.py` | `tests/unit/test_npm_fetcher_adapter.py` |
| `test_github_fetcher.py` | `tests/unit/test_github_fetcher_adapter.py` |
| `test_fetch_errors.py` | `tests/unit/test_fetch_errors.py` |
| `test_consumer.py` | `tests/unit/test_consumer_service.py` |
| `test_routes.py` | `tests/unit/test_ingest_router.py` |

---

## 12. Files to Delete After Migration

- `worker/` entire directory
