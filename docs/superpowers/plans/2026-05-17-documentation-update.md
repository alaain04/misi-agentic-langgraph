# Documentation Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply a consistent thin-README + detailed-docs pattern across all three app components and create a root-level project README.

**Architecture:** Each component gets a README with intro + quick start + doc links. Detailed content lives in `docs/` files. A root `README.md` is the single entry point for the whole project.

**Tech Stack:** Markdown only — no code changes.

---

## Files

| File | Action |
|---|---|
| `langgraph/README.md` | Create |
| `apps/backend/README.md` | Update — add intro line, add quick start commands |
| `apps/frontend/README.md` | Update — trim to intro + run + links, remove Vite boilerplate |
| `apps/workers/README.md` | Rewrite — replace template placeholder |
| `apps/workers/SoftwareArchitecture.md` | Delete |
| `apps/workers/docs/architecture.md` | Create — updated hexagonal architecture doc |

---

### Task 1: Create root README.md

**Files:**
- Create: `README.md` (repo root, i.e. `langgraph/README.md`)

- [ ] **Step 1: Write the file**

Full content:

```markdown
# misi-agentic

LangGraph-powered dependency risk analysis tool. Given a GitHub repository URL and a user concern (e.g. "check for outdated dependencies"), it clones the repo, generates a CycloneDX SBOM, presents an analysis plan for human approval, runs parallel ingestion subgraphs, and produces a structured risk report.

## Components

| Component | Description | README |
|---|---|---|
| backend | FastAPI + LangGraph pipeline, MongoDB job persistence | [apps/backend](apps/backend/README.md) |
| frontend | React + TypeScript web client | [apps/frontend](apps/frontend/README.md) |
| workers | NATS JetStream consumer for npm/GitHub entity fetching | [apps/workers](apps/workers/README.md) |

## API Reference

See [docs/api.md](docs/api.md) for the full REST API contract (endpoints, request/response schemas, TypeScript types).
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add root README"
```

---

### Task 2: Update backend README

**Files:**
- Modify: `apps/backend/README.md`

- [ ] **Step 1: Replace the file**

Full content:

```markdown
# Backend

LangGraph-powered dependency analysis API. Accepts a GitHub repository URL and a user concern, runs a multi-step agentic pipeline with human-in-the-loop approval, and returns a structured risk report.

## Quick Start

```bash
make sync      # install dependencies (uv)
make mongo     # start MongoDB
make dev       # start the API server (hot-reload)
```

## Docs

- [Architecture](docs/architecture.md) — request lifecycle, layers, job status, key design decisions
- [Graph Pipeline](docs/graphs.md) — LangGraph main graph and discovery subgraph
- [Development Setup](docs/development-setup.md) — prerequisites, environment variables
- [Code Conventions](docs/code-conventions.md)
- [API Reference](../../docs/api.md)
```

- [ ] **Step 2: Commit**

```bash
git add apps/backend/README.md
git commit -m "docs(backend): slim README to intro + quick start + links"
```

---

### Task 3: Update frontend README

**Files:**
- Modify: `apps/frontend/README.md`

- [ ] **Step 1: Replace the file**

Full content:

```markdown
# Frontend

React + TypeScript web client for the misi-agentic dependency analysis tool.

## Quick Start

```bash
pnpm install   # install dependencies
pnpm dev       # start Vite dev server
pnpm build     # build for production
pnpm lint      # run ESLint
```

## Docs

- [Code Conventions](docs/code-conventions.md)

## Architecture

- Components: `src/components/`
- Hooks: `src/hooks/`
- Shared utilities: `src/lib/`
- API client: `src/api/`
- Entry point: `src/App.tsx`

## API Integration

The backend exposes a REST API consumed by this client. See [docs/api.md](../../docs/api.md) for the full contract.
```

- [ ] **Step 2: Commit**

```bash
git add apps/frontend/README.md
git commit -m "docs(frontend): slim README, remove Vite boilerplate"
```

---

### Task 4: Rewrite workers README

**Files:**
- Modify: `apps/workers/README.md`

- [ ] **Step 1: Replace the file**

Full content:

```markdown
# Workers

NATS JetStream consumer that fetches npm and GitHub package data (issues, releases, security advisories) to support the dependency analysis pipeline.

## Stack

- Python 3.12 + FastAPI + uv
- NATS JetStream — message queue
- MongoDB — job tracking and entity cache
- Redis — rate limiting

## Quick Start

```bash
cp .env.sample .env   # fill in required values
make setup            # install dependencies (uv)
make docker-up        # start MongoDB, NATS, Redis
make dev              # start the API server + consumer
```

## Docs

- [Architecture](docs/architecture.md)
```

- [ ] **Step 2: Commit**

```bash
git add apps/workers/README.md
git commit -m "docs(workers): rewrite README, replace template placeholder"
```

---

### Task 5: Create workers/docs/architecture.md and delete SoftwareArchitecture.md

**Files:**
- Create: `apps/workers/docs/architecture.md`
- Delete: `apps/workers/SoftwareArchitecture.md`

- [ ] **Step 1: Write apps/workers/docs/architecture.md**

Full content:

```markdown
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
```

- [ ] **Step 2: Delete SoftwareArchitecture.md**

```bash
git rm apps/workers/SoftwareArchitecture.md
```

- [ ] **Step 3: Commit**

```bash
git add apps/workers/docs/architecture.md
git commit -m "docs(workers): replace SoftwareArchitecture.md with accurate docs/architecture.md"
```
