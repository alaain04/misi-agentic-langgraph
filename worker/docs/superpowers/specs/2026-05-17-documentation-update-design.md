---
date: 2026-05-17
status: Approved
scope: Documentation update — consistent README + docs pattern across all components
---

# Documentation Update Design

## Goal

Apply a consistent documentation pattern across the monorepo:
- Each component has a thin `README.md` (intro + quick start + doc links)
- Detailed content lives in `docs/` files
- A root `README.md` serves as the project entry point

---

## Files

| File | Action |
|---|---|
| `langgraph/README.md` | Create |
| `apps/backend/README.md` | Update — add intro line |
| `apps/frontend/README.md` | Update — remove Vite boilerplate |
| `apps/workers/README.md` | Rewrite — replace template placeholder |
| `apps/workers/SoftwareArchitecture.md` | Delete |
| `apps/workers/docs/architecture.md` | Create — updated hexagonal architecture doc |

---

## Content per file

### Root README

1. One-paragraph intro: misi-agentic is a LangGraph-powered dependency risk analysis tool.
2. Components table: backend (FastAPI + LangGraph + MongoDB), frontend (React/TS), workers (NATS JetStream entity-fetch consumer) — each with a link to their README.
3. Reference to `docs/api.md` for the API contract.

### Backend README

Keep the existing index structure. Add a one-sentence intro describing what the backend is.

### Frontend README

Keep: Component Architecture, Directory Structure, Development Flow, Code Conventions, API Integration sections.
Remove: the Vite template block (`# React + TypeScript + Vite` and everything after it).

### Workers README

1. Intro: NATS JetStream consumer that fetches npm and GitHub package data to support the analysis pipeline.
2. Quick start: `make setup`, `make docker-up`, `make dev`.
3. Doc links: `docs/architecture.md`.

### Workers docs/architecture.md

Based on the existing `SoftwareArchitecture.md` structure but updated to reflect the actual implementation:

**Keep:**
- Goals, architectural principles, layer diagram (4-layer hexagonal), dependency rules, decision log

**Update:**
- Adapters: NATS JetStream adapter, MongoDB job repository, MongoDB entity cache, npm fetcher, GitHub fetchers (issues/releases/advisories), Redis rate limiter
- Port contracts: add FetcherPort, RateLimitPort, EntityCachePort, JetStream methods on MessagingPort; remove UoW
- Infrastructure: MongoDB + NATS JetStream + Redis only (remove postgres, localstack, SNS/SQS, local filesystem)
- Config: show the actual env vars from the workers settings

**Remove:** PostgreSQL/SQLAlchemy, SNS+SQS/LocalStack, storage sections, SonarQube, Conventional Commits (those belong to backend)

---

## Pattern

```
README.md           ← intro + quick start + links
docs/
  architecture.md   ← system design, layers, adapters, data flow
  graphs.md         ← (backend only) mermaid graph diagrams
  development-setup.md  ← (backend only) env vars, prereqs
  code-conventions.md   ← short conventions list
```
