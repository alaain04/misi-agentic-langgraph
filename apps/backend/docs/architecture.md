# Architecture

## What it does

**misi-agentic** is a LangGraph-powered dependency risk analysis API. Given a GitHub repository URL and a user concern (e.g. "check for outdated dependencies"), it:

1. Clones the repo and generates a CycloneDX SBOM
2. Presents an analysis plan to the user for approval (human-in-the-loop)
3. Runs parallel ingestion subgraphs based on the approved plan
4. Cross-analyzes all results and produces a structured report

The API is non-blocking: `POST /analyze` immediately returns a `trace_id`. The client polls `GET /analyze/{trace_id}` for status updates.

---

## Request lifecycle

```
POST /analyze  { repo_url, concern }
  ├─ create Job (status=pending) → MongoDB
  ├─ asyncio.create_task(run_analysis)    ← fire-and-forget
  └─ return 202  { trace_id, status: "pending" }

Background: run_analysis
  ├─ discovery subgraph         clone → inspect → SBOM → summary
  ├─ orchestrator               LLM presents plan
  │   └─ interrupt()            ← graph suspends, job → awaiting_approval
  │
  POST /analyze/{trace_id}/chat  { message }
  │   └─ resume_analysis(user_message)
  │       ├─ classify intent    approve | change | cancel
  │       ├─ (loop if change)   re-plan with user instructions
  │       └─ approve → execution_planner
  │
  ├─ execution_planner          topological sort → parallel stages
  ├─ execute_plan ×N            fan-out via Send() — one per subgraph
  ├─ stage_advance              tracks completion, loops or advances
  ├─ cross_analyzer             correlates all ingestion results
  └─ report_reviewer            LLM review loop (max 2 iterations)

GET /analyze/{trace_id}
  └─ return { status, artifacts, result, graph }
```

---

## Layer responsibilities

| Layer | Path | Responsibility |
|---|---|---|
| **API** | `src/api/` | FastAPI routes, Pydantic schemas, response assembly |
| **DI wiring** | `src/services/dependencies.py` | `get_job_repo()` singleton factory (lru_cache) |
| **Domain ports** | `src/domain/ports/` | Abstract interfaces; `JobRepositoryPort` defines the DAO contract |
| **Models** | `src/models/` | `Job` entity; `JobStatus` enum; `to_doc()` converts to MongoDB format |
| **DAO** | `src/services/job_dao.py` | `JobDAO(JobRepositoryPort)` — MongoDB CRUD, artifact tracking, proposals |
| **DB** | `src/db/connection.py` | `AsyncMongoClient` singleton |
| **Runner** | `src/services/job_runner.py` | Bridges API → graph; streams graph events; drives job status transitions |
| **Main graph** | `src/main_graph/` | LangGraph pipeline — nodes, subgraphs, state |
| **Utils** | `src/utils/` | Settings (Pydantic, `.env`), LLM factory, Trivy wrapper |

---

## Dependency injection

Routes and graph nodes depend on `JobRepositoryPort`, not `JobDAO` directly.

```
src/services/dependencies.py
  └─ get_job_repo() → JobRepositoryPort     (lru_cache singleton)

src/api/routes.py
  └─ Depends(get_job_repo)                  (FastAPI DI for routes)

src/main_graph/nodes/execute_plan.py
src/main_graph/nodes/orchestrator.py
  └─ _dao = get_job_repo()                  (module-level singleton, patchable in tests)

src/services/job_runner.py
  └─ run_analysis(..., dao: JobRepositoryPort)   (parameter injection)
     resume_analysis(..., dao: JobRepositoryPort)
```

`src/api/dependencies.py` re-exports `get_job_repo` for the API layer. Graph nodes import directly from `src/services/dependencies.py` to keep the domain layer independent of the API layer.

---

## Job status lifecycle

```
pending
  └─ running          (analysis started)
       ├─ awaiting_approval   (orchestrator interrupted — waiting for user)
       │    └─ processing     (resume in flight)
       │         └─ running   (user approved, execution continuing)
       ├─ done
       ├─ failed
       └─ cancelled    (user cancelled via chat)
```

---

## Main graph

```
START
  │
  ▼
discovery          clone → inspect → lock-gen → SBOM → summary
  │
  ▼
orchestrator       LLM presents plan; interrupt() pauses for user approval
  │                (loop: re-plan if user requests changes)
  ▼
execution_planner  topological sort of selected subgraphs → execution stages
  │
  ▼ (Send × N — one per subgraph in current stage)
execute_plan       hydrate upstream results → invoke subgraph → record artifact
  │
  ▼
stage_advance      all subgraphs done? → advance to cross_analyzer
  │                still running? → loop back to execution_planner
  ▼
cross_analyzer     correlate results across all ingestion domains
  │
  ▼
report_reviewer    LLM review; loop back to cross_analyzer if feedback (max 2 iterations)
  │
  ▼
END
```

---

## Discovery subgraph

```
clone_repository
  ├─ (on error) ──────────────────────────────→ build_dependency_summary
  └─ inspector_agent         detect manifests, package manager, lock file
       ├─ (no lock file) ──→ lock_generator_agent → generate_sbom
       └─ (lock file) ─────→ generate_sbom
                                  └─ build_dependency_summary   LLM summary
                                        └─ END
```

| Node | Tool | What it does |
|------|------|------|
| `clone_repository` | Docker `alpine/git` | `git clone --depth=1` into a tmp dir |
| `inspector_agent` | LLM + filesystem tools | Detects package manager and lock file presence |
| `lock_generator_agent` | LLM + Docker | Runs `npm/pnpm/yarn install` to generate missing lock file |
| `generate_sbom` | Trivy / CycloneDX | Produces a CycloneDX SBOM from the lock file |
| `build_dependency_summary` | LLM | Generates a 2-5 sentence summary relevant to the user's concern |

---

## Ingestion subgraphs

Subgraphs are registered in `src/main_graph/subgraphs/ingestion_subgraphs/__init__.py`. The planner selects a subset at runtime based on the user's concern; each runs as a separate `execute_plan` invocation.

| Name | What it analyses |
|------|-----------------|
| `vulnerabilities` | Known CVEs and security advisories from the SBOM |
| `license_compliance` | Dependency license conflicts and compatibility issues |
| `repo` *(not in planner registry)* | GitHub commits, issues, releases, advisories via MCP |
| `runtime` *(not in planner registry)* | Docker container and runtime environment analysis |

`risk_score` and `recommendation` are pipeline-only subgraphs (not ingestion subgraphs) that the planner can include but which are resolved in the cross-analysis phase.

---

## Artifact tracking

Every backbone node records its execution to MongoDB under `job.artifacts`:

```json
{
  "node": "orchestrator",
  "status": "done | running | failed | cancelled",
  "started_at": "...",
  "completed_at": "...",
  "result_id": "...",        // ingestion subgraphs only
  "proposals": [...]         // orchestrator only
}
```

The frontend reads this to render a live execution DAG.

---

## Key design decisions

- **Non-blocking API** — `asyncio.create_task` keeps `POST /analyze` fast; 202 returns before graph execution.
- **Human-in-the-loop via `interrupt()`** — the orchestrator node pauses the graph; state is persisted in the LangGraph checkpointer; `resume_analysis` resumes via `Command(resume=...)`.
- **Parallel execution via `Send()`** — `task_dispatcher` fans out one `Send` per selected subgraph; `stage_advance` collects results.
- **Port interface for MongoDB** — `JobRepositoryPort` decouples callers from `JobDAO`; graph nodes are fully testable with `AsyncMock` without a real MongoDB connection.
- **Singleton DAO** — `get_job_repo()` is `lru_cache`-wrapped; routes and graph nodes share the same instance.
- **Layer independence** — graph nodes (`src/main_graph/`) import from `src/services/`, never from `src/api/`. The API layer depends on the service layer, not the reverse.
