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

This is a LangGraph-powered job queue API. The flow is:

1. `POST /analyze` receives a repo URL + concern → creates a `Job` (persisted to MongoDB with `status=pending`) → returns `job_id`
2. LangGraph graphs (in `graphs/`) are intended to pick up pending jobs and process them asynchronously

### Layer responsibilities

- **`src/api/*`** — FastAPI routes and request validation (Pydantic models inline)
- **`src/models/*`** — Entity schemas; `to_doc()` converts to MongoDB document format
- **`src/db/*`** — Database connection and access utilities
- **`src/utils/*`** — Configuration and utility functions
- **`src/graphs/*`** — LangGraph `StateGraph` definitions; see [GRAPHS.md](GRAPHS.md) for the full pipeline architecture

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
- Job IDs are auto-generated ObjectId strings (`str(ObjectId())`)
- The `graphs/` directory is the intended location for LangGraph `StateGraph` implementations that process jobs
