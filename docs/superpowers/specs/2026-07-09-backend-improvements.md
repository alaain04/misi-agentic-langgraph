# Backend Improvements Design

**Date:** 2026-07-09
**Status:** Approved

## Problem

Four data-quality and intelligence gaps in the backend:

1. `autopilot` mode is never persisted — you can't tell from a completed job whether it ran supervised or not.
2. HITL conversation history accumulates in LangGraph state but the conductor never sees it (prompt only shows the message count).
3. Findings reference tool results by UUID only — there's no way to trace a finding back to the source URL or log snippet without querying the LangGraph checkpoint.
4. Recommendations treat all dependencies as equal — transitive deps can't be fixed directly, and there's no mechanism to suggest alternatives or find the responsible direct dep.

## Goal

- Persist `autopilot` in the job document.
- Wire HITL conversation history into the conductor's LLM prompt.
- Replace `evidence_refs: list[str]` with full inline evidence objects on each finding.
- Make the conductor transitive-dep-aware and able to search the web for alternatives; persist the dependency tree for future frontend visualization.

## Scope

Backend-only changes. Frontend type updates required for items 1 and 3 (new metadata field, changed evidence shape).

---

## Section 1 — Autopilot in job metadata

### Data model

```python
class JobMetadata(BaseModel):
    repo_url: str
    concern: str
    autopilot: bool = False
```

### Route change (`routes.py`)

```python
job = Job(metadata=JobMetadata(
    repo_url=request.repo_url,
    concern=request.concern,
    autopilot=request.autopilot,
))
```

`autopilot` is already on `AnalysisRequest`; it just wasn't being stored on the job.

### Frontend type update (`api/types.ts`)

Add `autopilot: boolean` to `AnalysisMetadata`.

---

## Section 2 — HITL conversation surfaced to conductor

### Problem

`hitl_gate.py` returns `{"messages": [AIMessage, HumanMessage]}` which accumulates in `state["messages"]` via the `add_messages` reducer. The conductor prompt only shows `len(messages)` — the LLM never sees what was asked or answered.

### Fix (`conductor.py`)

Replace the count line with a formatted block:

```python
def _format_messages(messages: list) -> str:
    if not messages:
        return "No conversation history."
    parts = []
    for m in messages:
        role = "assistant" if isinstance(m, AIMessage) else "user"
        parts.append(f"[{role}]: {m.content}")
    return "\n".join(parts)
```

Prompt section changes from:
```
Conversation history: {len(state.get("messages") or [])} messages
```
to:
```
Conversation history:
{_format_messages(state.get("messages") or [])}
```

No storage changes. Messages stay in LangGraph state (LLM context) and in the artifact (UI). Both consumers are valid; this just makes the LLM consumer work.

---

## Section 3 — Full evidence objects inline with findings

### Problem

`FindingNote.evidence_refs: list[str]` stores opaque tool-result UUIDs. Tool results live only in the LangGraph checkpoint — once the job is done, there's no way to recover the source URL or relevant log excerpt from the persisted job document.

### New model

```python
class EvidenceRef(BaseModel):
    tool: str          # tool name that produced the evidence
    url: str | None    # advisory permalink, OSV URL, npm page, etc.
    log_snippet: str   # relevant excerpt from the tool output (< 500 chars)
```

```python
class FindingNote(BaseModel):
    dep_name: str
    severity: str
    description: str
    evidence: list[EvidenceRef]  # replaces evidence_refs: list[str]
```

### Conductor prompt update

Instruct the LLM to embed evidence inline when emitting a `FindingNote`:

> When emitting a FindingNote, populate the `evidence` list with one entry per supporting tool result. Set `tool` to the tool name, `url` to any advisory URL, CVE permalink, or OSV link present in the output (null if none), and `log_snippet` to the most relevant excerpt (max 400 characters).

### Report builder

`report_builder.py` passes findings through to the report as-is. The report's `findings[].evidence` array carries the full evidence objects. The `evidence_refs` field in the LLM prompt template is removed.

### Frontend type update (`api/types.ts`)

```typescript
export interface EvidenceRef {
  tool: string
  url: string | null
  log_snippet: string
}

// RiskFinding: replace evidence_refs: string[] with:
evidence: EvidenceRef[]
```

---

## Section 4 — Transitive dep awareness + web search + dep tree persistence

### 4a — New tool: `resolve_transitive_parent`

Reads the `npm_list --all` output (already in `state.tool_results` if `npm_list` was called) and for a given package name returns:

```python
{
  "package": "lodash",
  "is_direct": False,
  "brought_in_by": ["express", "body-parser"],  # direct dep parents
  "dep_chain": "express → accepts → lodash"
}
```

Registered as `resolve_transitive_parent(package_name)` in the tool registry.

This tool reads from the cloned repo's `node_modules` tree (via `npm ls --json --all`), so it requires `repo_path`.

### 4b — New tool: `web_search`

```python
@register("web_search", "Searches the web for current package alternatives, security advisories, or migration guides")
async def web_search(query: str) -> dict:
    ...  # Tavily API call
```

Requires env var `TAVILY_API_KEY`. Returns `{"results": [{"title", "url", "snippet"}], "query": query}`.

### 4c — Conductor prompt update

Add to the conductor system prompt:

> For every finding involving a transitive dependency (not listed in package.json `dependencies` / `devDependencies`):
> 1. Call `resolve_transitive_parent` to find the direct dep responsible.
> 2. Recommend updating that direct dep to a version that resolves the vulnerability.
> 3. If no safe version of the direct dep exists, call `web_search` to find alternative packages and include those in your recommendation.
>
> Never recommend updating a transitive dependency directly — instruct the user to update the direct dep instead (or its alternative).

### 4d — Dependency tree persistence

When `npm_list` is called in tool_runner, the result (the full dep tree) is stored in the existing `tool_results` state. Additionally, the job_runner should save this tree to a dedicated MongoDB collection `dep_trees`.

#### Collection schema

```
dep_trees collection:
{
  _id: job_id (str),
  created_at: datetime,
  tree: { ...npm_list output... }
}
```

#### New DAO method (`job_dao.py`)

```python
async def save_dep_tree(self, job_id: str, tree: dict) -> None:
    await self._dep_trees_col.replace_one(
        {"_id": job_id},
        {"_id": job_id, "created_at": datetime.now(UTC), "tree": tree},
        upsert=True,
    )

async def get_dep_tree(self, job_id: str) -> dict | None:
    doc = await self._dep_trees_col.find_one({"_id": job_id})
    return doc["tree"] if doc else None
```

#### New port method (`job_repository_port.py`)

```python
@abstractmethod
async def save_dep_tree(self, job_id: str, tree: dict) -> None: ...

@abstractmethod
async def get_dep_tree(self, job_id: str) -> dict | None: ...
```

#### Where to call `save_dep_tree`

In `job_runner.py`, inside the TOOL_RUNNER handler, after pushing the iteration artifact: if any result has `tool == "npm_list"` and no error, call `save_dep_tree(job_id, result.output)`.

```python
for tr in results:
    if tr.tool == "npm_list" and not tr.error:
        await dao.save_dep_tree(job_id, tr.output)
```

#### New API endpoint

```
GET /analyze/{trace_id}/dep-tree
→ 200: { "job_id": str, "tree": { ...npm_list output... } }
→ 404: if tree not yet available or job not found
```

---

## Pre-existing bug fix

`tool_runner.py` always calls `fn(repo_path=repo_path, **tc.args)`, but external API tools (`github_advisory`, `osv_lookup`, etc.) do not declare a `repo_path` parameter — they get a `TypeError` that is silently caught and returned as an error result. Fix: inspect the function signature before injecting.

```python
import inspect

sig = inspect.signature(fn)
kwargs = dict(tc.args)
if "repo_path" in sig.parameters:
    kwargs["repo_path"] = repo_path
output = await fn(**kwargs)
```

The new `web_search` tool also does not need `repo_path`, so this fix covers it too.

---

## Files changed

| File | Change |
|------|--------|
| `src/models/job.py` | Add `autopilot: bool = False` to `JobMetadata` |
| `src/models/conductor.py` | Add `EvidenceRef`; replace `evidence_refs` on `FindingNote` |
| `src/api/routes.py` | Pass `autopilot` to `JobMetadata` on creation; add dep-tree endpoint |
| `src/api/schemas.py` | Add `EvidenceRef`; update `RiskFinding`; add dep-tree response schema |
| `src/domain/ports/job_repository_port.py` | Add `save_dep_tree`, `get_dep_tree` |
| `src/services/job_dao.py` | Implement both; add `_dep_trees_col` |
| `src/services/job_runner.py` | Save dep tree when npm_list result available |
| `src/main_graph/nodes/conductor.py` | Format messages; update prompt for transitive deps + evidence instructions |
| `src/main_graph/nodes/tool_runner.py` | Fix repo_path injection (inspect signature) |
| `src/main_graph/nodes/report_builder.py` | Remove `evidence_refs` from prompt; pass `evidence` through |
| `src/main_graph/tools/package_files.py` | Add `resolve_transitive_parent` |
| `src/main_graph/tools/external_api.py` | Add `web_search` (Tavily) |
| `apps/frontend/src/api/types.ts` | Add `EvidenceRef`; add `autopilot` to `AnalysisMetadata` |

## Tests to write / update

- `test_job.py`: `JobMetadata` includes `autopilot`; dep-tree DAO methods
- `test_conductor.py`: conductor prompt includes conversation history when messages present
- `test_report_builder.py`: findings pass through with `evidence` field intact
- `test_package_files.py`: `resolve_transitive_parent` returns correct direct/transitive classification
- `test_job_runner.py`: dep tree saved when npm_list result present
