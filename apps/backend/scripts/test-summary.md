# Manual Node Debug — Test Summary

Date: 2026-06-28

## Prerequisites

- Colima running: `colima start`
- MongoDB running: `docker run -d --name mongo-debug -p 27017:27017 mongo:7`
- `.env` with `OPENAI_API_KEY` and `MONGODB_URI`

All commands run from `apps/backend/`.

---

## Results

### discovery
```
uv run python scripts/debug_subgraphs.py discovery
```
Clones `node-typescript-boilerplate` via Docker (`alpine/git`), runs inspector agent, generates a CycloneDX SBOM via `npm sbom` in `node:24-alpine`, saves to MongoDB.  
**Result: PASS** — full SBOM with components and dependency tree returned.

**Bugs found and fixed:**
- `DockerContainerAdapter` was passing `sh -c` as args to the image entrypoint instead of overriding it — added `--entrypoint sh` to the `docker run` command.
- `generate_sbom_service` ran `npm sbom` without a working directory — prefixed command with `cd /workspace &&`.

---

### planner
```
uv run python scripts/debug_subgraphs.py planner
```
Calls `investigation_planner_service` with canned SBOM and discovery summary. `interrupt()` is patched to return `"approve"` immediately, bypassing HITL. DAO calls are absorbed by `AsyncMock`.  
**Result: PASS** — LLM generated 4 hypotheses (vulnerability + blast_radius for express, vulnerability + reachability for lodash) with correct skill assignments.

---

### dispatch
```
uv run python scripts/debug_subgraphs.py dispatch
```
Calls `skill_dispatcher` with a canned `InvestigationPlan` (express + lodash). Pure function, no LLM or services.  
**Result: PASS** — 3 `Send` tasks produced: `VulnerabilitySkill` × 2 (one per dep) + `MaintainerTrustSkill` × 1. Skills gated by missing `repo_path` are correctly filtered out by `can_run()`.

---

### skill
```
uv run python scripts/debug_subgraphs.py skill MaintainerTrustSkill
```
Calls a skill directly via `SkillContext` with `services={}`. MCP client is absent so the skill gracefully falls back to an empty data dict.  
**Result: PASS** — returned 1 evidence item (`maintainer_signal`, medium severity, low confidence 0.2 due to missing MCP data). Graceful degradation confirmed.

---

### correlate
```
uv run python scripts/debug_subgraphs.py correlate
```
Chains `evidence_correlator` → `finding_reviewer` → `report_builder` with 3 canned evidence items (medium severity, no HITL triggered).  
**Result: PASS**
- Correlator produced 2 `RiskFinding` objects with LLM-generated summaries and alternatives.
- Reviewer auto-approved (no high/critical findings).
- Report builder assembled final `analysis_report` sorted by risk score.

---

## Nodes not covered

| Node | Reason |
|---|---|
| `investigation_planner` (full HITL) | Requires graph checkpointer + interrupt/resume cycle. Test via the frontend flow. |
| `evidence_collector` | No-op fan-in node — nothing to debug. |
| `skill_executor` | Thin wrapper around skills; use `skill` mode instead. |
