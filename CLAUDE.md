# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync          # install dependencies
uv run dev       # run the API server
uv run lint      # lint (ruff check)
uv run format    # format (ruff format)
uv run test      # run tests
docker compose up -d   # start MongoDB
```

## Documentation

- **[docs/architecture.md](docs/architecture.md)** — system overview, request lifecycle, layer responsibilities, job status lifecycle, environment setup
- **[docs/graphs.md](docs/graphs.md)** — LangGraph pipeline: current ProjectDiscovery subgraph (implemented) + full planned pipeline

## Quick orientation

This is a LangGraph-powered dependency risk analysis API:

1. `POST /analyze` receives a repo URL + concern → creates a `Job` in MongoDB (`status=pending`) → fires `asyncio.create_task` → returns `202` with `trace_id`
2. The background task (`job_runner.py`) invokes the LangGraph subgraph, transitioning status to `running` → `done` | `failed`
3. `GET /analyze/{trace_id}` lets the client poll for status

Layer map: `src/api/` → routes · `src/models/` → entities · `src/db/` → connection · `src/services/` → DAO + runner · `src/graphs/` → LangGraph subgraphs · `src/utils/` → config + LLM factory

## Key conventions

- All I/O is async (`AsyncMongoClient`, async route handlers, `httpx.AsyncClient` in nodes)
- Node names in `constants.py`, routing logic in `routes.py` — no raw strings in `graph.py`
- HTTP nodes use `RetryPolicy(max_attempts=3, backoff_factor=2.0)`
- Error paths set `discovery_error` and short-circuit; exceptions do not bubble out of nodes
- `job_dao.py` owns all MongoDB access — graph nodes do not touch the database
