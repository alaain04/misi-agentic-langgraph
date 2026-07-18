## CLAUDE.md

This file provides guidance for Claude Code when working with this repository.

## Project Structure

```
apps/
  backend/   # Python/LangGraph API (FastAPI, LangGraph, MongoDB)
  frontend/  # React + TypeScript + Vite web client
docs/
  api.md              # REST API entry point (endpoints + links to domain docs)
  backend/
    analysis.md       # POST /analyze, GET /analyze/{trace_id}, full response shapes
    jobs.md           # GET /jobs, JobStatus enum
    hitl.md           # Human-in-the-loop gates, POST /analyze/{trace_id}/chat
    artifacts.md      # Per-node artifact shapes (live execution tracking)
    report.md         # AnalysisReport + RiskFinding shapes
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
- [API entry point](docs/api.md) — endpoints overview and job lifecycle
- [Analysis flow](docs/backend/analysis.md) — start job, poll status, full response shapes
- [HITL chat](docs/backend/hitl.md) — handling awaiting_approval state and /chat
- [Artifacts](docs/backend/artifacts.md) — per-node progress data for pipeline visualization
- [Report](docs/backend/report.md) — final analysis_report shape

## Integration

- Backend exposes REST API at `http://localhost:8000` — see [docs/api.md](docs/api.md)
- Frontend consumes the backend API; keep them independently runnable
