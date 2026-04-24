# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync          # install dependencies
uv run dev       # run the API server
uv run lint      # lint (ruff check)
uv run format    # format (ruff format)
uv run test      # run tests
```

## Architecture

This is a LangGraph-powered dependency risk analysis API. The request lifecycle is:

1. `POST /analyze` receives a repo URL + concern → creates a `Job` (persisted to MongoDB with `status=pending`) → fires `asyncio.create_task` → returns `202` with `trace_id`
2. The background task (`job_runner.py`) runs the LangGraph subgraph, updating job status to `running` → `done` | `failed`
3. `GET /analyze/{trace_id}` lets the client poll for status

### Layer responsibilities

- **`src/api/*`** — FastAPI routes and request validation (Pydantic models inline)
- **`src/models/*`** — Entity schemas; `to_doc()` converts to MongoDB document format
- **`src/db/*`** — Database connection and access utilities
- **`src/utils/*`** — Configuration and utility functions
- **`src/services/job_dao.py`** — MongoDB CRUD for `Job`; `save_result()` persists the full graph output
- **`src/services/job_runner.py`** — Bridge between the API and the graph; manages job status transitions
- **`src/graphs/*`** — LangGraph `StateGraph` definitions; see [GRAPHS.md](GRAPHS.md) for the full pipeline architecture

### Job status lifecycle

```
pending → running → done
                 └→ failed
```

### Environment variables

Copy `.env.example` to `.env`:

| Variable | Required | Default |
|---|---|---|
| `OPENAI_API_KEY` | Yes | — |
| `MONGODB_URI` | Yes | — |
| `LANGSMITH_API_KEY` | No | — |
| `LANGSMITH_PROJECT` | No | — |

## Key design decisions

- All I/O is async (AsyncMongoClient, async FastAPI route handlers)
- Job IDs are auto-generated ObjectId strings (`str(ObjectId())`) and double as the `trace_id` returned to callers
- Fire-and-forget via `asyncio.create_task` keeps POST response fast (202 returns before graph runs)
- Graph output is stored in the MongoDB job document under a `result` field on completion
- Node names and routing logic are defined in `constants.py` and `routes.py` — no raw strings in `graph.py`
