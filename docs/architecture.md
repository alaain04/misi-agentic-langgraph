# Architecture

## What it does

**misi-agentic** is a LangGraph-powered dependency risk analysis API. Given a GitHub repository URL and a concern (e.g. "check for outdated dependencies"), it returns a structured analysis of the project's JavaScript dependencies — package manager, direct deps, transitive counts, and an LLM-generated summary.

The API is non-blocking: `POST /analyze` immediately returns a `trace_id` and the analysis runs in the background. The client polls `GET /analyze/{trace_id}` until `status` is `done` or `failed`.

---

## Request lifecycle

```
POST /analyze  { repo_url, concern, token? }
  │
  ├─ validate inputs (Pydantic)
  ├─ create Job (status=pending) → MongoDB
  ├─ asyncio.create_task(run_discovery)   ← fire-and-forget
  └─ return 202  { trace_id, status: "pending" }

Background: run_discovery
  ├─ JobDAO.update_status → running
  ├─ project_discovery_subgraph.ainvoke(...)
  └─ JobDAO.save_result | update_status(failed)

GET /analyze/{trace_id}
  └─ return { trace_id, status [, result] }   ← client polls
```

---

## Layer responsibilities

| Layer | Path | Responsibility |
|---|---|---|
| **API** | `src/api/` | FastAPI routes, request validation, Pydantic models |
| **Models** | `src/models/` | Entity schemas; `to_doc()` converts to MongoDB document format |
| **DB** | `src/db/` | `AsyncMongoClient` singleton |
| **DAO** | `src/services/job_dao.py` | MongoDB CRUD for `Job`; `save_result()` persists full graph output |
| **Runner** | `src/services/job_runner.py` | Bridge between API and graph; manages job status transitions |
| **Graphs** | `src/graphs/` | LangGraph `StateGraph` definitions — see [graphs.md](graphs.md) |
| **Utils** | `src/utils/` | Settings (Pydantic, `.env`), LLM factory |

---

## Job status lifecycle

```
pending → running → done
                 └→ failed
```

Jobs are stored in MongoDB collection `jobs`. The `_id` field is an `ObjectId` string and doubles as the `trace_id` returned to callers.

---

## Key design decisions

- **All I/O is async** — `AsyncMongoClient`, async FastAPI handlers, `httpx.AsyncClient` in graph nodes.
- **Fire-and-forget** — `asyncio.create_task` keeps the `POST` response fast; the 202 returns before graph execution begins.
- **ObjectId as trace_id** — job IDs are auto-generated `str(ObjectId())` and exposed directly to callers; no secondary ID scheme.
- **Graph output in MongoDB** — completed job documents gain a `result` field containing the full graph output.
- **No raw strings in graph code** — node names live in `constants.py` and routing logic in `routes.py`.
- **Clone-free GitHub access** — the graph uses the GitHub REST, Trees, and Contents APIs to inspect repos without cloning.

---

## Environment variables

Copy `.env.example` to `.env`:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | LLM calls in `build_dependency_summary` |
| `MONGODB_URI` | Yes | `mongodb://localhost:27017/misi-langgraph` | Job persistence |
| `LANGSMITH_API_KEY` | No | — | LangSmith tracing |
| `LANGSMITH_PROJECT` | No | — | LangSmith project name |

Run MongoDB locally with:

```bash
docker compose up -d
```

---

## Full pipeline (planned)

The current implementation runs only the **ProjectDiscovery** subgraph. The planned full pipeline adds a planner agent and parallel specialized subgraphs:

```
POST /analyze
  └─ ProjectDiscovery subgraph     (implemented ✓)
       └─ Planner agent            (planned)
            └─ Task Dispatcher     (parallel fan-out via Send)
                 ├─ Registry subgraph      (npm/PyPI vulnerability checks)
                 ├─ Repo subgraph          (static analysis: secrets, misconfigs)
                 ├─ Runtime subgraph       (Dockerfile, k8s, env vars)
                 ├─ Risk Score subgraph    (aggregates signals → structured score)
                 └─ Recommendation subgraph (prioritised remediation steps)
                      └─ Final Report → MongoDB (status=complete)
```

See [graphs.md](graphs.md) for the detailed graph architecture.
