# Backend 3-Layer Refactor Design

**Date:** 2026-07-11  
**Status:** Approved

---

## Overview

Refactor the backend main graph from its current 5-node flat structure into three explicit, independently-persisted layers: **Preparation**, **Analysis**, and **Report**. Each layer is a compiled LangGraph subgraph. MongoDB is the inter-layer data bus — each subgraph writes its full output to the DB and only passes a result ID through `MainState`, keeping state lean and each layer independently resumable.

---

## Architecture

```
main_graph
  START
    │
    ▼
  prep_subgraph          clone → inspect → index → SBOM → summary
    │ writes PrepResult to MongoDB
    ▼
  analysis_subgraph      conductor (ReAct) → fan-out → domain agents → fan-in → conductor
    │ reads PrepResult, writes AnalysisResult to MongoDB
    ▼
  report_subgraph        report_conductor (ReAct) → tools → report_conductor
    │ reads AnalysisResult, writes ReportResult to MongoDB
    ▼
  END
```

### MainState (lean)

```python
class MainState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str
    autopilot: bool

    # Layer result IDs (inter-layer bus)
    prep_result_id: NotRequired[str]
    analysis_result_id: NotRequired[str]
    report_result_id: NotRequired[str]

    # Control
    messages: Annotated[list, add_messages]
    cancelled: NotRequired[bool]
    discovery_error: NotRequired[str | None]
```

All rich payloads (SBOM, evidence bundles, findings, report) live in MongoDB under the result IDs. Nodes in a downstream layer load what they need from DB at startup — they do not receive it via state.

---

## Layer 1 — Preparation

Reuses the existing discovery subgraph with one addition: repo indexing before the SBOM step.

```
clone_repository
  ├─ (error) ──────────────────────────────────────────────────→ build_dependency_summary
  └─ inspector_agent        detect manifests, package manager, lock file
       ├─ (no lock file) ──→ lock_generator_agent ─┐
       └─ (lock file) ─────────────────────────────┘
                                  │
                                  ▼
                          index_repository       ← NEW (runs regardless of lock file path)
                                  │
                                  ▼
                           generate_sbom
                                  │
                                  ▼
                       build_dependency_summary
                                  │
                                  ▼
                          save_prep_result → END
```

### New node: `index_repository`

After the repo is cloned and before SBOM generation, all source files are chunked and embedded into an **in-memory vector store** (LangChain `InMemoryVectorStore` with the project's configured embeddings model). The vector store is serialized and persisted to MongoDB as part of `PrepResult`.

This index is shared by both the Analysis and Report layers via `vector_store_id`.

### PrepResult (MongoDB)

```json
{
  "repo_path": "...",
  "project_metadata": { "name": "...", "version": "...", "node_version": "..." },
  "manifest_files": ["package.json", "package-lock.json"],
  "detected_package_manager": "npm|pnpm|yarn",
  "dependency_graph": {
    "direct": { "express": "4.18.2" },
    "transitive": { "debug": { "version": "4.3.4", "brought_in_by": ["express"] } }
  },
  "sbom_cyclonedx": { ... },
  "sbom_result_id": "...",
  "discovery_summary": "...",
  "vector_store_id": "..."
}
```

---

## Layer 2 — Analysis

A ReAct conductor loop that forms hypotheses from the concern, dispatches domain subagents in parallel, reviews their evidence, and iterates until confident enough to finalize.

```
analysis_subgraph
  START
    │
    ▼
  analysis_conductor      ReAct — reads PrepResult from DB, forms hypotheses + dispatch plan
    │                     (no HITL gate — autopilot only for now)
    ▼
  agent_dispatcher        deterministic — converts dispatch plan into Send() × N
    │
    ├─────────────────────────────────────────────────────────────────┐
    ▼                                                                 ▼
  domain_agent (× N, parallel)                        web_research_agent (fallback)
  each: own ReAct loop + curated tool set             ReAct + web_search + osv_lookup + github_advisory
    │                                                                 │
    └──────────────────────────┬──────────────────────────────────────┘
                               ▼
                    evidence_collector    fan-in
                               │         each domain agent writes its own EvidenceBundle
                               │         to MongoDB and returns { bundle_id };
                               │         evidence_collector merges bundle_ids into state
                               ▼
                    analysis_conductor    reviews bundles
                               │         dispatch more agents OR finalize
                               │         if finalize → writes AnalysisResult to MongoDB
                               ▼
                              END
```

### Domain Agent Registry (static)

| Agent | Domain | Tools |
|---|---|---|
| `vulnerability_agent` | `vulnerabilities` | `npm_audit`, `osv_lookup`, `github_advisory`, `search_code` |
| `maintenance_agent` | `maintenance` | `unmaintained_packages`, `high_risk_packages`, `package_reputation`, `search_code` |
| `supply_chain_agent` | `supply_chain` | `typosquat_detection`, `resolve_transitive_parent`, `package_json`, `search_code` |
| `license_agent` | `license` | *(new: license checker tool)*, `search_code` |
| `web_research_agent` | `*` (fallback) | `web_search`, `github_advisory`, `osv_lookup` |

If the conductor's `agent_type` does not match any registered agent, it routes to `web_research_agent`.

### Shared tool: `search_code`

Available to all domain agents. Queries the vector store (loaded from `vector_store_id` in PrepResult) for source files that reference a given package or pattern.

```python
async def search_code(query: str, top_k: int = 10) -> list[dict]:
    # Returns: [{ "file": "src/api/client.ts", "line": 12, "snippet": "import axios from 'axios'" }]
```

### Conductor Dispatch Instruction

```python
class AgentDispatch(BaseModel):
    domain: str                      # e.g. "vulnerabilities", "supply_chain", "custom-theme"
    hypothesis: str                  # what to investigate and why
    packages_to_focus: list[str]     # conductor narrows scope; empty = all deps
    agent_type: str                  # registered name or "web_research" for fallback
```

### EvidenceBundle (per agent, written to MongoDB)

```json
{
  "domain": "vulnerabilities",
  "hypothesis": "express may have known CVEs affecting the /auth routes",
  "findings": [
    {
      "dep_name": "express",
      "severity": "high",
      "description": "...",
      "recommendation": "...",
      "evidence": [{ "tool": "osv_lookup", "url": "...", "log_snippet": "..." }]
    }
  ],
  "summary": "Found 2 high-severity CVEs in express@4.18.2 ...",
  "confidence": 0.9
}
```

The conductor only sees `summary`, `findings`, and `confidence` — never raw tool output from subagents.

### AnalysisResult (MongoDB)

```json
{
  "concern": "...",
  "findings": [ ...merged FindingNote list... ],
  "evidence_bundles": [ ...EvidenceBundle list... ],
  "iteration_count": 2
}
```

---

## Layer 3 — Report

A ReAct conductor loop that reads the `AnalysisResult`, enriches each finding with internet research and code impact data, and produces the final structured report.

```
report_subgraph
  START
    │
    ▼
  report_conductor      ReAct — reads AnalysisResult + PrepResult from DB
    │
    ▼
  report_tool_runner    parallel tool execution
    │
    └──→ report_conductor   loop until finalized
                │
                ▼
         writes ReportResult to MongoDB → returns { report_result_id }
                │
                ▼
               END
```

### Report Conductor Tools

| Tool | Purpose |
|---|---|
| `web_search(query)` | Find safer alternatives, migration guides, CVE details |
| `code_impact(package_name)` | Query vector store — files + snippets that import/use the package |
| `get_findings(severity?)` | Read findings from AnalysisResult (optionally filtered by severity) |

`code_impact` uses the same `vector_store_id` from `PrepResult` — no separate indexing step needed in this layer.

### ReportResult (MongoDB)

```json
{
  "concern": "...",
  "generated_at": "...",
  "executive_summary": "...",
  "overall_risk_level": "critical|high|medium|low|none",
  "findings": [
    {
      "dep_name": "express",
      "severity": "high",
      "description": "...",
      "recommendation": "Upgrade to express@4.21.0 or migrate to fastify",
      "alternatives": ["fastify", "hono"],
      "affected_files": ["src/api/server.ts:3", "src/middleware/auth.ts:1"],
      "evidence": [{ "tool": "osv_lookup", "url": "...", "log_snippet": "..." }]
    }
  ],
  "recommendations": [
    "Upgrade express to >=4.21.0 to patch CVE-XXXX-XXXX",
    "..."
  ]
}
```

No HITL gate in this layer — the report runs to completion autonomously.

---

## Data Flow Summary

```
POST /analyze { repo_url, concern }
  │
  ▼
main_graph starts
  │
  ├─ prep_subgraph
  │    clones repo, indexes source files, generates SBOM
  │    → MongoDB: PrepResult { prep_result_id }
  │
  ├─ analysis_subgraph
  │    conductor reads PrepResult
  │    dispatches N domain agents in parallel
  │    each agent writes EvidenceBundle to MongoDB
  │    conductor loops until confident
  │    → MongoDB: AnalysisResult { analysis_result_id }
  │
  └─ report_subgraph
       conductor reads AnalysisResult + PrepResult
       enriches findings (web search, code impact)
       → MongoDB: ReportResult { report_result_id }

GET /analyze/{trace_id}
  └─ reads job status + report_result_id → fetches ReportResult from MongoDB
```

---

## What Changes vs. Current Architecture

| Current | New |
|---|---|
| Flat 5-node main graph | 3 nested subgraphs with clear layer boundaries |
| `MainState` carries all data | `MainState` carries only result IDs |
| Single conductor handles all tools | Conductor dispatches domain subagents; each has its own ReAct loop |
| Flat tool registry shared by everything | Per-agent curated tool sets + shared `search_code` tool |
| Report = single LLM call | Report = ReAct loop with web search + code impact tools |
| No code impact analysis | Code impact via vector store indexed at Prep time |
| No inter-layer persistence | Each layer writes full output to MongoDB before next layer starts |

---

## Open Questions (deferred)

- **License checker tool**: no existing license tool — needs implementation or a third-party library (e.g. `license-checker` npm CLI).
- **Vector store persistence**: `InMemoryVectorStore` is ephemeral per process. For multi-worker deployments, consider serializing embeddings to MongoDB GridFS or using a persistent store (Chroma, pgvector).
- **HITL gates**: removed for now. Can be reintroduced between layers (e.g. plan approval before Analysis, draft review before Report) in a later iteration.
- **Max conductor iterations**: current Analysis conductor caps at 10 iterations. This limit may need tuning based on concern complexity.
