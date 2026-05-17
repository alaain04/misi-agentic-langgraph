# Workers Architecture

**Architecture style:** Hexagonal (Ports & Adapters) + Domain-Driven Design

---

## What it does

The workers service is a NATS JetStream consumer. It receives `FetchMessage` jobs from the main backend pipeline, fetches npm and GitHub package data (issues, releases, security advisories), caches results in MongoDB, and tracks job completion.

API endpoints (`POST /ingest`, `GET /status/{job_id}`) allow the backend to trigger batch fetch jobs and poll for progress.

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     API Layer                           │
│  POST /ingest   GET /status/{job_id}                    │
│  api/routers/ingest_router.py · api/schemas.py          │
└──────────────────────┬──────────────────────────────────┘
                       │ calls
┌──────────────────────▼──────────────────────────────────┐
│              Application Services Layer                 │
│  IngestService — create job, publish to NATS            │
│  ConsumerService — pull messages, dispatch fetchers     │
└──────────────────────┬──────────────────────────────────┘
                       │ uses ports (interfaces)
┌──────────────────────▼──────────────────────────────────┐
│                    Domain Layer                         │
│  Models: Job, FetchMessage                              │
│  Ports:  MessagingPort · CachePort · JobRepositoryPort  │
│          EntityCachePort · FetcherPort · RateLimitPort  │
│              domain/models · domain/ports/              │
└──────────────────────┬──────────────────────────────────┘
                       │ implemented by
┌──────────────────────▼──────────────────────────────────┐
│                   Adapters Layer                        │
│  NATSJetStreamAdapter    RedisCacheAdapter              │
│  MongoJobRepository      MongoEntityCacheAdapter        │
│  NpmFetcherAdapter       GithubFetcherAdapter (×3)      │
│  RedisRateLimiter                                       │
│                      adapters/                          │
└──────────────────────┬──────────────────────────────────┘
                       │ configured by
┌──────────────────────▼──────────────────────────────────┐
│                Infrastructure / Config                  │
│  config/settings.py · .env · docker-compose.yml         │
│  MongoDB · NATS JetStream · Redis                       │
└─────────────────────────────────────────────────────────┘
```

---

## Domain Layer

### Models (`domain/models/`)

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
    entity_type: str     # "npm" | "github_issues" | "github_releases" | "github_advisories"
    name: str
```

### Ports (`domain/ports/`)

| Port | Contract |
|---|---|
| `MessagingPort` | `connect/disconnect/publish/subscribe/acknowledge` + JetStream: `pull_subscribe/pull_fetch/ack/nak/term/add_stream` |
| `CachePort` | `get/set/delete/exists/flush` |
| `JobRepositoryPort` | `create / record_success / record_failure / get_status / delete` |
| `EntityCachePort` | `save(collection, name, doc) / get(collection, name)` |
| `FetcherPort` | `fetch(client, name) -> dict` + `collection: str` + `rate_group: str` |
| `RateLimitPort` | `acquire(group: str) -> None` |

---

## Adapters Layer

| Adapter | Port | Notes |
|---|---|---|
| `NATSJetStreamAdapter` | `MessagingPort` | JetStream pull consumer; creates `ENTITY_FETCH` stream on connect |
| `RedisCacheAdapter` | `CachePort` | JSON serialisation; key TTL support |
| `MongoJobRepository` | `JobRepositoryPort` | Collection: `ingest_jobs` |
| `MongoEntityCacheAdapter` | `EntityCachePort` | Upsert by name; records `fetched_at` |
| `NpmFetcherAdapter` | `FetcherPort` | Collection: `npm_package_cache`; rate group: `npm` |
| `GithubIssuesFetcherAdapter` | `FetcherPort` | Collection: `github_issues_cache`; rate group: `github` |
| `GithubReleasesFetcherAdapter` | `FetcherPort` | Collection: `github_releases_cache`; rate group: `github` |
| `GithubAdvisoriesFetcherAdapter` | `FetcherPort` | Collection: `github_advisories_cache`; rate group: `github` |
| `RedisRateLimiter` | `RateLimitPort` | Lua sliding-window script; configurable windows per group |

---

## Application Services

**`IngestService`** — creates a job in MongoDB, publishes one `FetchMessage` per package to NATS. On publish failure, deletes the job and raises.

**`ConsumerService`** — sets up a JetStream pull subscription, spawns N concurrent worker tasks. Each task pull-fetches messages, dispatches to the correct `FetcherPort` by `entity_type`, saves the result via `EntityCachePort`, and acks/naks/terms based on error type.

### Error handling

| Error | Action |
|---|---|
| `RateLimitError` | `nak(delay=exc.delay)` |
| `TransientFetchError` (retries remaining) | `nak(delay=exponential_backoff)` |
| `TransientFetchError` (retries exhausted) | `term()` + `record_failure()` |
| `PermanentFetchError` | `term()` + `record_failure()` |
| Unexpected exception | `term()` + `record_failure()` |

---

## Infrastructure

| Service | Image | Purpose | Port |
|---|---|---|---|
| `api` | Dockerfile.dev | FastAPI + consumer | 8000 |
| `mongodb` | mongo:7 | Job tracking + entity cache | 27017 |
| `redis` | redis:7-alpine | Rate limiting + cache | 6379 |
| `nats` | nats:2.10-alpine | JetStream message queue | 4222 / 8222 |

---

## Configuration

Key environment variables (`config/settings.py`):

| Variable | Default | Purpose |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection |
| `MONGODB_DB` | `misi` | Database name |
| `NATS_URL` | `nats://localhost:4222` | NATS connection |
| `NATS_STREAM_NAME` | `ENTITY_FETCH` | JetStream stream |
| `NATS_MAX_DELIVER` | `5` | Max delivery attempts before `term()` |
| `GITHUB_TOKEN` | — | GitHub API token |
| `WORKER_CONCURRENCY` | `5` | Concurrent consumer tasks |
| `NPM_RATE_WINDOWS` | `[(60,500),(3600,5000)]` | npm rate limit windows (seconds, max) |
| `GITHUB_RATE_WINDOWS` | `[(60,100),(3600,5000)]` | GitHub rate limit windows |

---

## Dependency Rules

```
api          → application_services, domain.ports (via DI only)
services     → domain.ports
domain       → nothing (pure Python)
adapters     → domain.ports, external libraries
config       → nothing (pure pydantic-settings)
```
