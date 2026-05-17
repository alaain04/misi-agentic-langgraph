# Software Architecture Document

**Service:** Python Service Boilerplate
**Architecture style:** Hexagonal (Ports & Adapters) + Domain-Driven Design (DDD)
**Last updated:** 2026-03-10
**Status:** Active — update this document whenever a significant architectural decision is made.

---

## Table of Contents

1. [Goals](#1-goals)
2. [Architectural Principles](#2-architectural-principles)
3. [Layer Diagram](#3-layer-diagram)
4. [Layer Descriptions](#4-layer-descriptions)
5. [Port Contracts](#5-port-contracts)
6. [Adapter Implementations](#6-adapter-implementations)
7. [Data Flow](#7-data-flow)
8. [Infrastructure](#8-infrastructure)
9. [Messaging Architecture](#9-messaging-architecture)
10. [Storage Architecture](#10-storage-architecture)
11. [Security Considerations](#11-security-considerations)
12. [Dependency Rules](#12-dependency-rules)
13. [Decision Log](#13-decision-log)
14. [TODO / Planned Extensions](#14-todo--planned-extensions)

---

## 1. Goals

- Provide a **clean, replaceable** foundation where infrastructure (DB, cache, messaging, storage) can be swapped without touching business logic.
- Enable **testability**: domain and application layers are framework-agnostic and trivially unit-testable with mocks.
- Enforce **DDD discipline**: entities, value objects, and aggregates live in the domain; no ORM or HTTP concerns bleed in.
- Support **async-first** Python (FastAPI + asyncpg + aiobotocore) for high-throughput I/O.

---

## 2. Architectural Principles

| Principle | Application |
|-----------|-------------|
| **Dependency Inversion** | Domain depends only on port abstractions; adapters depend on ports |
| **Single Responsibility** | Each file/class has one reason to change |
| **Open/Closed** | Add new adapters without modifying domain or application code |
| **Ports & Adapters** | Ports = interfaces; Adapters = concrete implementations |
| **Explicit over implicit** | All dependencies are injected via FastAPI `Depends()` |
| **Async throughout** | All I/O operations are async (SQLAlchemy async, aiobotocore, redis.asyncio) |

---

## 3. Layer Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     API Layer                           │
│  FastAPI routers · Pydantic schemas · DI dependencies   │
│              api/routers · api/schemas.py               │
└──────────────────────┬──────────────────────────────────┘
                       │ calls
┌──────────────────────▼──────────────────────────────────┐
│               Application Services Layer                │
│   Orchestrates domain operations, UoW, events           │
│           services/application_services/                │
└──────────────────────┬──────────────────────────────────┘
                       │ uses ports (interfaces)
┌──────────────────────▼──────────────────────────────────┐
│                    Domain Layer                         │
│  Entities · Value Objects · Domain Events               │
│  Ports: CachePort · RepositoryPort · UoWPort            │
│          MessagingPort · StoragePort                    │
│              domain/models · domain/ports/              │
└──────────────────────┬──────────────────────────────────┘
                       │ implemented by
┌──────────────────────▼──────────────────────────────────┐
│                   Adapters Layer                        │
│  Redis · SQLAlchemy/PostgreSQL · AWS SNS+SQS · NATS     │
│                 Local FS · (S3 future)                  │
│                      adapters/                          │
└─────────────────────────────────────────────────────────┘
                       │ configured by
┌──────────────────────▼──────────────────────────────────┐
│                Infrastructure / Config                  │
│     config/settings.py · .env · docker-compose.yml     │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Layer Descriptions

### 4.1 Domain Layer (`domain/`)

The innermost layer. **No framework dependencies** (no FastAPI, no SQLAlchemy, no boto3).

| Sublayer | Purpose |
|----------|---------|
| `domain/models/` | Entities (identity-based) and Value Objects (equality-based). Pure Python dataclasses or Pydantic BaseModels. |
| `domain/ports/` | Abstract base classes (ABC) defining contracts that adapters must fulfil. |

**Rule:** Domain code never imports from `adapters/`, `api/`, or `services/`.

### 4.2 Application Services Layer (`services/application_services/`)

Thin orchestration layer. Contains **no business rules** — those live in the domain.

Responsibilities:
- Coordinate multiple repositories via the Unit of Work.
- Publish domain events via the messaging port.
- Apply caching read-through / write-invalidation strategies.
- Map between domain entities and API schemas.

### 4.3 API Layer (`api/`)

FastAPI-specific incoming adapter:

| File | Purpose |
|------|---------|
| `api/routers/` | One router file per domain aggregate. Route handlers are thin — they delegate to application services. |
| `api/schemas.py` | Pydantic v2 request/response models. Never shared with domain models. |
| `api/dependencies.py` | Constructs and wires all adapters; exposes them via `Depends()`. |

### 4.4 Adapters Layer (`adapters/`)

Concrete implementations of the domain ports.

---

## 5. Port Contracts

All ports live in `domain/ports/` and are abstract base classes.

### CachePort
```python
get(key) → Optional[Any]
set(key, value, ttl) → None
delete(key) → None
exists(key) → bool
flush() → None
```

### RepositoryPort[T]
```python
get_by_id(id) → Optional[T]
get_all() → List[T]
add(entity) → T
update(entity) → T
delete(id) → None
```

### UnitOfWorkPort
```python
async with uow:
    await uow.commit()
    await uow.rollback()
```

### MessagingPort
```python
connect() → None
disconnect() → None
publish(topic, message, attributes) → str  # returns message ID
subscribe(queue, handler) → None
acknowledge(receipt_handle) → None
```

### StoragePort
```python
upload(file, destination_path, content_type) → str  # returns URL
download(source_path) → bytes
delete(path) → None
exists(path) → bool
get_url(path, expires_in) → str
```

---

## 6. Adapter Implementations

### 6.1 Cache: Redis

| File | `adapters/cache/redis_adapter.py` |
|------|----------------------------------|
| Library | `redis[hiredis]` (async) |
| Serialisation | JSON |
| Config | `REDIS_URL` |

### 6.2 Database: PostgreSQL via SQLAlchemy

| File | `adapters/db/sqlalchemy/` |
|------|--------------------------|
| ORM models | `models.py` — DeclarativeBase, separate from domain entities |
| Repository base | `sqlalchemy_repository.py` — generic, subclass per aggregate |
| Unit of Work | `sqlalchemy_unit_of_work.py` — wraps AsyncSession |
| Migrations | Alembic in `migrations/` |
| Driver | `asyncpg` |
| Config | `DATABASE_URL` |

### 6.3 Messaging: AWS SNS + SQS (primary)

| File | `adapters/messaging/sns_sqs_adapter.py` |
|------|-----------------------------------------|
| Library | `aiobotocore` |
| Publish | SNS `publish()` → JSON-serialised |
| Consume | SQS long-polling (20s), up to 10 msgs/call |
| Local dev | LocalStack (`AWS_ENDPOINT_URL=http://localhost:4566`) |
| Config | `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `SNS_TOPIC_ARN`, `SQS_QUEUE_URL` |

### 6.4 Messaging: NATS (alternative)

| File | `adapters/messaging/nats_adapter.py` |
|------|--------------------------------------|
| Library | `nats-py` |
| Switch | Set `MESSAGING_BACKEND=nats` in `.env` |
| Config | `NATS_URL` |

### 6.5 Storage: Local Filesystem (primary in dev)

| File | `adapters/storage/local_storage_adapter.py` |
|------|---------------------------------------------|
| Base dir | Configurable via `STORAGE_LOCAL_DIR` |
| Swap path | Implement `S3StorageAdapter(StoragePort)` and bind in `api/dependencies.py` |
| Config | `STORAGE_BACKEND=local`, `STORAGE_LOCAL_DIR`, `STORAGE_BASE_URL` |

---

## 7. Data Flow

### HTTP Request Flow

```
Client
  → FastAPI Router (api/routers/)
    → Validate with Pydantic schema (api/schemas.py)
      → Application Service (services/application_services/)
        → Repository Port (domain/ports/repository_port.py)
          → SQLAlchemy Repository (adapters/db/sqlalchemy/)
            → PostgreSQL
        → Cache Port → Redis (on cache hit, short-circuit)
        → Messaging Port → SNS Publish (on domain event)
      → Map domain entity → response schema
  → HTTP Response
```

### Async Message Consumption Flow

```
SQS Queue (long-poll)
  → SNSSQSAdapter.subscribe()
    → _process_message()
      → handler (MessageHandler callable)
        → Application Service
          → UnitOfWork → Repository → PostgreSQL
        → Acknowledge (SQS delete_message)
```

---

## 8. Infrastructure

### Services (docker-compose.yml)

| Service | Image | Purpose | Port |
|---------|-------|---------|------|
| `api` | Dockerfile.dev | FastAPI application | 8000 |
| `postgres` | postgres:16-alpine | Primary database | 5432 |
| `redis` | redis:7-alpine | Cache layer | 6379 |
| `localstack` | localstack/localstack:3 | AWS SNS, SQS, S3 emulation | 4566 |
| `nats` | nats:2.10-alpine | Alternative messaging | 4222 / 8222 |

### Dockerfile Strategy

| File | Purpose |
|------|---------|
| `Dockerfile` | Production — multi-stage, non-root user, minimal image |
| `Dockerfile.dev` | Development — includes dev deps, hot-reload via volume mount |

---

## 9. Messaging Architecture

### NATS vs SNS+SQS — same purpose, different hosting model

Both NATS and AWS SNS+SQS are **messaging brokers** and implement the same `MessagingPort` contract. They are fully interchangeable at the application level. The difference is purely operational:

| | AWS SNS + SQS | NATS |
|---|---|---|
| **Purpose** | Async pub/sub messaging | Async pub/sub messaging |
| **Hosting model** | AWS-managed cloud service | Self-hosted (you run the server) |
| **Local development** | Emulated by **LocalStack** | Own Docker container (`nats:2.10-alpine`) |
| **Production** | AWS infrastructure (no server to manage) | Your own server / Kubernetes pod |
| **Why LocalStack?** | SNS/SQS don't exist as standalone software — LocalStack fakes the entire AWS API locally so dev code is identical to production code | Not needed — NATS runs the same way in dev and prod |

**LocalStack is not a messaging service.** It is an AWS API emulator that runs locally so you can develop against SNS, SQS, and S3 without a real AWS account or internet connection. Your application code (`aiobotocore` calls) is 100% identical whether it hits LocalStack or real AWS — only `AWS_ENDPOINT_URL` changes.

**Switching between them** requires only a single env var change — no application code changes:

```bash
MESSAGING_BACKEND=sns_sqs   # uses SNS+SQS (via LocalStack in dev, real AWS in prod)
MESSAGING_BACKEND=nats       # uses NATS (same Docker container in dev and prod)
```

### Topic / Queue Naming Convention (SNS + SQS)

```
{service}.{aggregate}.{event}

Examples:
  payments.order.created
  inventory.product.updated
  notifications.user.registered
```

### Message Envelope (SNS wraps to SQS)

```json
{
  "Type": "Notification",
  "MessageId": "uuid",
  "TopicArn": "arn:aws:sns:...",
  "Message": "{\"event\": \"order.created\", \"payload\": {...}}",
  "Timestamp": "2026-01-01T00:00:00.000Z"
}
```

Application code receives the unwrapped `payload` dict after `SNSSQSAdapter._process_message()` unpacks the envelope.

### Error Handling

- Failed messages are NOT acknowledged → SQS returns them after `VisibilityTimeout` expires.
- Configure a **Dead Letter Queue (DLQ)** in production for poison messages.
- Set `maxReceiveCount` on the SQS queue redrive policy.

---

## 10. Storage Architecture

### Current: Local Filesystem

- Files stored under `STORAGE_LOCAL_DIR` (default: `/tmp/storage`).
- URLs returned as `STORAGE_BASE_URL/{path}`.
- Suitable for local development and single-instance deployments.

### Production Upgrade Path: AWS S3

1. Implement `adapters/storage/s3_storage_adapter.py` implementing `StoragePort`.
2. Set `STORAGE_BACKEND=s3` and `S3_BUCKET_NAME` in environment.
3. Update `api/dependencies.py` `_build_storage_adapter()` to return `S3StorageAdapter`.
4. No changes to domain, application, or router code.

---

## 11. Security Considerations

| Area | Approach |
|------|---------|
| Secrets | Never commit `.env`; use `.env.sample` as template |
| Credentials | Use IAM roles in production; `AWS_ACCESS_KEY_ID` only for local dev |
| API auth | Add OAuth2/JWT middleware in `main.py` and `api/dependencies.py` |
| Non-root container | Production `Dockerfile` runs as `appuser` |
| Pre-commit | `detect-secrets` hook blocks accidental secret commits |
| Dependency scanning | Add `pip-audit` or `safety` to CI pipeline |

---

## 12. Dependency Rules

```
api          → application_services, domain.ports, adapters (via DI only)
services     → domain.ports
domain       → nothing (pure Python)
adapters     → domain.ports, external libraries
config       → nothing (pure pydantic-settings)
```

Violations of these rules are architectural debt and should be flagged in code review.

---

## 13. Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-10 | Use async SQLAlchemy with asyncpg | Full async stack, no thread-pool workarounds |
| 2026-03-10 | AWS SNS+SQS as primary messaging | Production-grade, managed, fan-out support |
| 2026-03-10 | NATS as alternative messaging | Lightweight, self-hosted option for teams avoiding AWS |
| 2026-03-10 | Local filesystem as default storage | Zero-config for development; S3-swappable via port |
| 2026-03-10 | Pydantic v2 for settings | Native `.env` parsing, validation, type coercion |
| 2026-03-10 | Ruff over black+isort+flake8 | Single fast tool covering linting + formatting |
| 2026-03-10 | mypy strict mode | Catch type errors early; consistent with domain model approach |

---

## 14. TODO / Planned Extensions

- [ ] `adapters/storage/s3_storage_adapter.py` — S3 implementation of `StoragePort`
- [ ] `adapters/auth/` — JWT token validation adapter
- [ ] `tests/unit/` — Unit test scaffold for domain and application services
- [ ] `tests/integration/` — Integration tests with testcontainers (Postgres, Redis, LocalStack)
- [ ] Health check endpoints for each adapter (DB, Redis, messaging)
- [ ] OpenTelemetry traces wired to all adapters
- [ ] Dead Letter Queue (DLQ) setup in `scripts/localstack-init.sh`
- [ ] `services/application_services/` — concrete service implementations per domain aggregate
