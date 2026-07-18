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

## Memory

You have access to Engram persistent memory via MCP tools (mem_save, mem_search, mem_session_summary, etc.).

- You MUST call `mem_search` before answering any question that touches prior work, a past decision, or a previously-diagnosed bug — not only when the user says "remember."
- Save proactively after significant work — don't wait to be asked.
- After any compaction or context reset, call `mem_context` to recover session state before continuing.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->

## Tool usage discipline

CodeGraph and Engram only pay off if they're actually called before falling back to grep/Read or answering from memory-less first principles. Both get skipped for the same handful of rationalizations — catch these before acting:

| Thought | Reality |
|---|---|
| "It's just a quick grep" | `codegraph_explore` is a single call and also returns callers/callees grep can't find. Use it first, not after grep comes up short. |
| "I already know where this lives" | Confirm anyway — file layout and call sites drift as the codebase changes. |
| "This doesn't look related to past work" | `mem_search` is cheap; a missed prior decision or already-diagnosed bug is expensive. Search before concluding there's nothing there. |
| "I'll search memory if I get stuck" | Search first, not as a fallback — the point of memory is to avoid redoing analysis, not to rescue a dead end. |

Self-check before using Read/Grep/Glob to understand or locate code: did I run `codegraph_explore` first?
Self-check before answering a question that could touch prior decisions, bugs, or discussions: did I run `mem_search` first?
