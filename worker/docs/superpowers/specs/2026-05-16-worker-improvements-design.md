# Worker Improvements Design

**Date:** 2026-05-16  
**Branch:** feat/worker  
**Scope:** Code quality fixes + Redis rate limiter + GitHub fetchers

---

## Goals

1. Fix six code quality issues in the existing worker.
2. Replace the in-memory `TokenBucket` with a Redis-backed multi-window sliding-window rate limiter that works correctly under horizontal scaling.
3. Add three GitHub fetchers (`github_issues`, `github_releases`, `github_advisories`) sharing one rate group.
4. Add Docker Compose with NATS (JetStream) and Redis.

---

## Code Quality Fixes

Six targeted changes, each in its own file:

### 1. Registry encapsulation (`fetchers/__init__.py`)

Replace the raw dict-of-tuples with a `FetcherEntry` dataclass:

```python
@dataclass
class FetcherEntry:
    fetch_fn: FetchFn
    collection: str
    rate_group: str
```

Expose three public functions and keep `_REGISTRY` private:
- `get(entity_type) -> FetcherEntry` — raises `ValueError` on unknown type
- `entity_types() -> list[str]`
- `rate_groups() -> set[str]`

`consumer.py` and `ingest.py` must only call these public functions.

### 2. Pydantic validation (`routers/ingest.py`)

Replace the manually-called `validate_entity_type()` method with a Pydantic v2 `@model_validator(mode='after')` on `IngestRequest`. Validation runs automatically on instantiation; no call site needed.

### 3. Silent failure in npm fetcher (`fetchers/npm.py`)

`_get` currently returns `None` on non-200 or network error; callers silently produce empty dicts. Change `_get` to raise `RuntimeError` on exhausted retries. The consumer's existing `except Exception` handler will catch and record failure.

Because `_get` no longer returns `None`, the `fetch` function must also remove its defensive conditionals: `reg_resp.json() if reg_resp else {}` becomes `reg_resp.json()`, and likewise for the downloads response.

### 4. Ghost config (`main.py`)

`main.py` hardcodes a `"github"` bucket even though no GitHub fetcher existed. After this change, `main.py` derives rate groups dynamically:

```python
rate_configs = {
    "npm": settings.npm_rate_windows,
    "github": settings.github_rate_windows,
}
buckets = {group: ... for group in fetchers.rate_groups()}
```

If a rate group appears in the registry but not in `rate_configs`, startup raises `ValueError`.

### 5. Type annotation (`consumer.py`)

`_worker(sub, ...)` — `sub` is typed as `nats.js.JetStreamPullSubscription`. Add the import and annotation.

### 6. Test imports (`tests/unit/`)

All test files import modules inside test functions (deferred). Move all imports to module level. This prevents state leakage and makes test failures easier to diagnose.

---

## Redis Rate Limiter

### Motivation

The current `TokenBucket` is per-process. Multiple worker replicas each enforce their own limit independently, multiplying the effective rate. A Redis-backed limiter centralizes enforcement.

Additionally, many APIs enforce multiple windows simultaneously (e.g. 1 000 req/min AND 10 000 req/hr). A single RPS counter cannot model this correctly — the worker could exhaust the hourly quota in the first minute.

### Algorithm: sliding window with Lua

Each rate group has one or more `(window_seconds, max_requests)` pairs. For each window, Redis holds a sorted set keyed by `ratelimit:{group}:{window_secs}`, scored by Unix timestamp.

A single Lua script atomically:
1. Trims expired entries from every window (`ZREMRANGEBYSCORE`).
2. Counts remaining entries in every window.
3. If **all** windows have capacity: adds the new request ID to every sorted set, sets TTL, returns `1`.
4. If **any** window is full: returns `0` without writing anything.

No partial consumption is possible.

On rejection, the caller computes the earliest time a slot opens in the saturated window (`ZRANGE key 0 0 WITHSCORES`) and sleeps until then, then retries all windows.

### Interface

```python
class RateLimiter:
    def __init__(self, windows: dict[str, list[tuple[int, int]]]) -> None: ...
    async def acquire(self, rate_group: str) -> None: ...
```

`windows` maps rate group name → list of `(window_secs, max_req)` pairs.

`acquire` blocks until a slot is available across all windows for the given group. It is safe to call concurrently from multiple asyncio tasks.

### Redis client (`redis_client.py`)

Mirrors `nats_client.py`: module-level `_client`, `connect()`, `close()`, `get_client()`. Uses `redis.asyncio` (redis-py ≥ 5).

### FetchFn signature change

The current signature is:

```python
FetchFn = Callable[[AsyncClient, str, TokenBucket, int], Awaitable[dict]]
```

It becomes:

```python
FetchFn = Callable[[AsyncClient, str, RateLimiter, int], Awaitable[dict]]
```

All fetchers receive a `RateLimiter` and call `await rate_limiter.acquire("npm")` (or `"github"`). Each fetcher hardcodes its own rate group string — `npm.py` calls `acquire("npm")`, all three GitHub fetch functions call `acquire("github")`.

---

## GitHub Fetchers

### New entity types

| Entity type | Collection | Rate group |
|---|---|---|
| `github_issues` | `github_issues_cache` | `github` |
| `github_releases` | `github_releases_cache` | `github` |
| `github_advisories` | `github_advisories_cache` | `github` |

### Implementation: `fetchers/github.py`

Shared helper:

```python
async def _get(client, url, headers, rate_limiter, max_retries) -> list[dict]:
    """Fetch all pages from a GitHub paginated endpoint. Raises on exhausted retries."""
```

Uses `Link: <url>; rel="next"` header for pagination. On 429, reads `Retry-After` and sleeps. On exhausted retries, raises `RuntimeError`.

Three exported fetch functions:

**`fetch_issues(client, name, rate_limiter, max_retries)`**
- `name` format: `"owner/repo"`
- Endpoint: `GET /repos/{owner}/{repo}/issues?state=all&since={N days ago ISO8601}&per_page=100`
- Returns: `{"issues": [list of issue dicts]}`

**`fetch_releases(client, name, rate_limiter, max_retries)`**
- Endpoint: `GET /repos/{owner}/{repo}/releases?per_page=100` (paged)
- Filter client-side: keep releases where `published_at >= M days ago`
- Returns: `{"releases": [filtered list]}`

**`fetch_advisories(client, name, rate_limiter, max_retries)`**
- Endpoint: `GET /repos/{owner}/{repo}/security-advisories?per_page=100` (paged)
- Returns: `{"advisories": [list of advisory dicts]}`

All use `Authorization: Bearer {token}` via `settings.github_token`.

---

## Configuration (`config.py`)

New fields added to `Settings`:

```python
redis_url: str = "redis://localhost:6379"
github_token: str  # no default — required
github_issues_lookback_days: int = 30
github_releases_lookback_days: int = 90
npm_rate_windows: list[tuple[int, int]] = [(60, 500), (3600, 5000)]
github_rate_windows: list[tuple[int, int]] = [(60, 100), (3600, 5000)]
```

Pydantic v2 parses `list[tuple[int, int]]` from a JSON env var:
```
NPM_RATE_WINDOWS='[[60,500],[3600,5000]]'
```

Removed fields: `npm_rate_limit_rps`, `github_rate_limit_rps` (superseded by rate windows).

---

## Docker Compose

File: `worker/docker-compose.yml`

Services:
- **nats** — `nats:latest` with `-js` flag for JetStream, port `4222`
- **redis** — `redis:7-alpine`, port `6379`

No application service — the worker runs locally via `uv run`.

---

## Files Changed

| File | Type | Change |
|---|---|---|
| `src/rate_limiter.py` | modify | Redis sliding-window `RateLimiter` |
| `src/redis_client.py` | new | async Redis connection |
| `src/fetchers/__init__.py` | modify | `FetcherEntry` dataclass, public API |
| `src/fetchers/github.py` | new | 3 fetch functions + shared helper |
| `src/fetchers/npm.py` | modify | raise instead of returning `None` |
| `src/config.py` | modify | new settings, remove old RPS settings |
| `src/consumer.py` | modify | use `RateLimiter`, type `sub` |
| `src/main.py` | modify | derive rate groups from registry, init Redis |
| `src/routers/ingest.py` | modify | `@model_validator`, no private registry access |
| `docker-compose.yml` | new | NATS + Redis |
| `tests/unit/` | modify | fix deferred imports |

---

## What Is Not Changing

- NATS JetStream pull-consumer pattern — no changes to message flow
- MongoDB job tracking (`jobs.py`) — two-step update is acceptable; `$expr` conditional flip is intentional
- `db.py` lazy-init pattern — consistent with `nats_client.py`
- Test strategy — unit tests with mocks remain; no new integration tests in this scope
