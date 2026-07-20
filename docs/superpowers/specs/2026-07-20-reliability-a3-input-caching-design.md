# Spec: A3 — Input Caching by Commit SHA

**Date:** 2026-07-20
**Workstream:** A (Reliability & Determinism) — chunk A3.
**Parent:** `docs/superpowers/roadmap.md`
**Depends on:** nothing (independent — can land any time). Complements A1/A2/A4.
**Scope:** Backend only.

## Problem

The pipeline recomputes deterministic, expensive inputs on every run of the same
repo: the dependency graph, the `npm audit` output, and the CodeGraph index.
These are pure functions of the repo at a given commit — recomputing them wastes
time and money and is one avoidable source of run-to-run drift (e.g. an
`npm install` that resolves slightly differently, or a transient audit/registry
difference). Caching them by commit SHA makes the evidence base stable and cuts
cost/latency.

This is primarily a **cost/latency + reproducibility** win; it is not required
for correctness. It is independent of A1/A2/A4 and can be scheduled whenever
convenient.

## Design

- **Key:** `(repo_url, commit_sha)`. The discovery subgraph already clones at a
  specific commit; capture that SHA as the cache key.
- **What to cache:**
  - the resolved dependency graph (`build_dependency_graph` output),
  - the `npm audit` JSON,
  - the CodeGraph index artifact.
- **Where:** reuse the existing persistence layer (MongoDB via the DAO) with a
  dedicated cache collection, or a content-addressed store keyed by the SHA —
  fits the hexagonal layout as a new port/adapter so the cache backend is
  swappable.
- **Invalidation:** commit SHA is the natural cache key — a new commit is a new
  key, so there is no stale-invalidation problem. Add a max-age TTL only if
  registry/advisory freshness for `npm audit` matters (audit results *do* change
  as new advisories publish — so the audit cache should carry a short TTL, while
  the dependency graph and CodeGraph index — pure functions of the source — can
  cache indefinitely).
- **Miss path:** on cache miss, compute as today and populate the cache; on
  error, always fall back to recompute (cache is an optimization, never a
  correctness dependency).

## Testing

- Unit-test the cache key derivation and the get/put/miss/fallback logic with a
  fake cache adapter (no DB).
- Unit-test that a cache-read error degrades to recompute rather than failing the
  job.
- Unit-test the audit-TTL vs. indefinite-cache distinction (audit entry expires;
  dependency-graph entry does not).

## Success criteria

- Second analysis of the same repo+commit reuses cached dependency graph,
  audit (within TTL), and CodeGraph index — measurable latency/cost drop.
- Cache miss and cache error both fall back to correct recompute.
- No behavior change to findings other than reduced input drift.
- Full backend suite, ruff, mypy green.

## Out of scope

- Caching LLM outputs (semantics differ — an LLM cache would freeze
  non-deterministic reasoning and is a separate design with its own tradeoffs).
- Cross-repo/global sharing of cache entries.
