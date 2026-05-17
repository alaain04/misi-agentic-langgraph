## CLAUDE.md

This file provides guidance for Claude Code when working with this repository.

## Project Structure

```
apps/
  backend/   # Python/LangGraph API (FastAPI, LangGraph, MongoDB)
  frontend/  # React + TypeScript + Vite web client
  workers/   # Python NATS JetStream entity-fetch consumer
docs/
  api.md     # REST API reference (backend endpoints + TypeScript types)
http-docs/   # Runnable HTTP request files (httpYac / REST Client)
```

## Component knowledge

Before working on any component, read its documentation:

**Backend** (`apps/backend/`)
- [Architecture](apps/backend/docs/architecture.md) — request lifecycle, layers, DI, job status
- [Graph pipeline](apps/backend/docs/graphs.md) — LangGraph nodes and subgraphs
- [Development setup](apps/backend/docs/development-setup.md) — env vars, prerequisites
- [Code conventions](apps/backend/docs/code-conventions.md)

**Frontend** (`apps/frontend/`)
- [Code conventions](apps/frontend/docs/code-conventions.md)
- [API reference](docs/api.md) — REST contract consumed by this client

**Workers** (`apps/workers/`)
- [Architecture](apps/workers/docs/architecture.md) — hexagonal layers, ports, adapters, NATS JetStream, MongoDB

## Integration

- Backend exposes REST API at `http://localhost:8000` — see [docs/api.md](docs/api.md)
- Workers API at `http://localhost:8001` (default) — see [http-docs/workers.http](http-docs/workers.http)
- Frontend consumes the backend API; keep them independently runnable
