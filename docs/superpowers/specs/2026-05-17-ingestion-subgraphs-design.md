# Ingestion Subgraphs Design

**Date:** 2026-05-17
**Status:** Approved

---

## Context

misi-agentic is a LangGraph-powered dependency risk analysis API for JavaScript projects. It clones a GitHub repo, generates a CycloneDX SBOM, and runs parallel analysis pipelines to evaluate dependency risks.

This spec defines the full ingestion subgraph architecture, addressing:
- Which capabilities (subgraphs) to run and what they analyze
- A three-stage execution model with per-dependency fan-out
- Two new agentic nodes (risk_ranker, recommendation)
- Flexible planning: the orchestrator proposes a customized capability set and optional dep filter based on the user's concern

**Thesis objectives driving this design:**
1. Identify high-risk dependencies in JavaScript projects
2. Identify critical risk parameters per dependency
3. Analyze use and impact of each dependency in the user's project
4. Recommend alternatives for high-risk dependencies

---

## Capability Inventory

### SBOM-level (run once per analysis, always available)

| Subgraph | Status | What it analyzes |
|---|---|---|
| `vulnerabilities` | done | Trivy scan of cloned repo → CVE findings for all packages |
| `license_compliance` | done | CycloneDX SBOM → license compatibility for all packages |

These always run against the full SBOM. Trivy is more efficient running once for the whole project than per-package. Their results are available to `risk_ranker` for per-dep lookup.

### Per-dep risk analysis (Stage 1 fan-out)

| Subgraph | Status | What it analyzes |
|---|---|---|
| `registry` | new | npm metadata for one dep: last publish date, weekly downloads, deprecation flag, maintenance status |
| `repo` | complete analyze logic; missing `graph.py` + registration | GitHub signals for one dep: commit frequency, open issue count, release cadence, security advisories |
| `runtime` | complete analyze logic; missing `graph.py` + registration | Clones dep source, runs its own test/lint scripts inside Docker → pass rate, lint error count |

All three run once per dependency in scope. `runtime` is compute-intensive (Docker); the planner can omit it when the user's concern doesn't involve code quality.

`repo` and `runtime` resolve the GitHub repository URL from `sbom_cyclonedx.components[dependency_name].externalReferences` (VCS type), not from `upstream_results["registry"]`. This keeps all Stage 1 subgraphs independent and fully parallel — no DEPENDS_ON chain needed within Stage 1.

**`registry` and `repo` delegate raw data fetching to the workers service** (see Workers Integration section). They do not call npm or GitHub APIs directly.

### Per-high-risk-dep impact analysis (Stage 2 fan-out)

| Subgraph | Status | What it analyzes |
|---|---|---|
| `impact` | new | For one dep in the **user's project**: counts import/require usages (static scan) + computes transitive blast radius from the SBOM dependency tree |

`impact` is optional (planner-selectable). It runs only for deps marked high-risk by `risk_ranker`, against the user's cloned project (`repo_path` in state).

### Pipeline synthesis (Stage 3)

| Node | Type | Role |
|---|---|---|
| `risk_score` | standard node | Final composite risk score per dep, combining Stage 1 + Stage 2 signals |
| `recommendation` | agentic node | Searches for alternatives to high-risk deps; produces actionable per-dep advice |

`risk_score` and `recommendation` are not ingestion subgraphs — they are always part of Stage 3 and are not planner-selectable. They receive all upstream results as input.

### Planner-selectable capabilities

The orchestrator's plan selects from:

```
vulnerabilities, license_compliance, registry, repo, runtime, impact
```

`risk_score` and `recommendation` are implicit — always included in Stage 3.

---

## Plan Structure

The plan changes from a flat list of subgraph names to a structured object:

```python
class Plan(TypedDict):
    subgraphs: list[str]           # selected capability types
    dep_filter: list[str] | None   # specific package names to scope; None = all direct deps
```

**dep_filter** scopes per-dep subgraphs (`registry`, `repo`, `runtime`, `impact`). SBOM-level subgraphs (`vulnerabilities`, `license_compliance`) always run for all packages regardless of filter.

The orchestrator's existing re-plan loop handles dep_filter: if the user says "only check react and lodash," the planner re-runs with those names as extra_instructions and emits a dep_filter.

**Example plans by concern:**

| Concern | Subgraphs | dep_filter |
|---|---|---|
| Security risks | vulnerabilities, registry, repo | null |
| Maintenance / outdated deps | registry, repo | null |
| Code quality of a specific dep | runtime, impact | ["lodash"] |
| Full analysis of top deps | vulnerabilities, license_compliance, registry, repo, runtime, impact | ["react", "express", "axios"] |

---

## Pipeline and Execution Model

```
discovery
  └─ orchestrator  (interrupt → user approves plan + dep_filter)
       └─ execution_planner
            │
            ├─ Stage 1 — Ingestion (parallel):
            │    ├─ vulnerabilities        ← once, whole SBOM
            │    ├─ license_compliance     ← once, whole SBOM
            │    ├─ registry × N_deps      ← per dep in scope
            │    ├─ repo × N_deps          ← per dep in scope
            │    └─ runtime × N_deps       ← per dep in scope (if selected)
            │
            ├─ risk_ranker  (AGENTIC NODE — cross-analyzes all Stage 1 outputs,
            │                scores deps, selects high-risk set for Stage 2)
            │
            ├─ Stage 2 — Impact (parallel, only if `impact` selected):
            │    └─ impact × N_high_risk   ← per high-risk dep: static usage +
            │                                blast radius in user's project
            │
            └─ Stage 3 — Synthesis (pipeline):
                 ├─ risk_score        ← composite score per dep (Stage 1 + Stage 2)
                 └─ recommendation    ← AGENTIC: searches alternatives, produces advice

cross_analyzer  (assembles unified structured report from Stage 3 artifacts)
  └─ report_reviewer  (LLM review loop, max 2 iterations)
  └─ END
```

### Stage types

| Stage | Execution | Gated by |
|---|---|---|
| Stage 1 | Parallel fan-out via `Send()`, one per (subgraph, dep) pair | execution_planner |
| risk_ranker | Single node, runs after Stage 1 completes | stage_advance detecting Stage 1 done |
| Stage 2 | Parallel fan-out via `Send()`, one per high-risk dep | risk_ranker output |
| Stage 3 | Sequential pipeline nodes | Stage 2 complete (or Stage 1 if impact not selected) |

### Per-dep fan-out

`execute_plan` currently receives a subgraph name. Under this design it receives a `(subgraph_name, dep_name)` pair. The `execution_planner` generates these pairs by crossing selected per-dep subgraphs with the dep scope (all direct deps or dep_filter).

SBOM-level subgraphs (`vulnerabilities`, `license_compliance`) are emitted as single entries without a dep_name.

### risk_ranker selection threshold

`risk_ranker` selects deps for Stage 2 impact analysis. Selection criteria:
- Any dep with a CRITICAL or HIGH CVE finding
- Any dep flagged deprecated or unmaintained by `registry`
- Any dep with failing tests (exit_code ≠ 0) from `runtime`
- The top-3 deps by composite preliminary score if none of the above apply (ensures Stage 2 always runs when impact is selected)

---

## State Changes

### `AnalysisState` (base for all ingestion subgraphs)

Add one field:

```python
class AnalysisState(TypedDict):
    sbom_cyclonedx: dict[str, Any]
    discovery_summary: str
    concern: str
    upstream_results: NotRequired[dict[str, Any]]
    repo_path: NotRequired[str]
    dependency_name: NotRequired[str]   # NEW — which dep this invocation analyzes
```

Per-dep subgraphs (`registry`, `repo`, `runtime`, `impact`) read `dependency_name` to scope their work. SBOM-level subgraphs ignore it.

### `RuntimeState`

Remove `direct_dependencies` (the old approach took `direct_deps[0]` as the primary dep). Under this design `runtime` reads `dependency_name` from `AnalysisState` instead:

```python
class RuntimeState(AnalysisState):
    result_id: NotRequired[str]
```

`runtime/nodes/analyze.py` must be updated to resolve the package name and version from `dependency_name` + `sbom_cyclonedx.components` rather than `state["direct_dependencies"][0]`.

### `MainState`

Add `dep_filter` to carry the plan's dep scope through execution:

```python
dep_filter: NotRequired[list[str] | None]
```

---

## New and Modified Components

### New subgraphs

**`registry`** — wraps a single `analyze` node that:
1. Calls `POST /ingest` on the workers API with `entity_types: ["npm"]`, `items: [dependency_name]`
2. Polls `GET /status/{job_id}` until `status == "done"` or `"failed"` (async polling with backoff)
3. Reads result from the shared MongoDB `npm_package_cache` collection (keyed by package name)
4. Extracts: `last_publish` (from `registry_data.time.modified`), `weekly_downloads`, `is_deprecated` (from `registry_data.deprecated`), `maintainers_count`

Follows the same subgraph pattern as `vulnerabilities` (graph.py + __init__.py with GRAPH_NAME, DEPENDS_ON, describe()).

**`impact`** — wraps a single `analyze` node that:
1. Scans `repo_path` for `import … from 'dep'` and `require('dep')` patterns → usage count + file list
2. Traverses `sbom_cyclonedx.dependencies` to compute how many other packages depend on this dep (blast radius count)

### Subgraphs to complete

**`repo`** already has `analyze` node logic but needs to be reworked to use workers for raw data fetching:
1. Extracts `owner/repo` from `sbom_cyclonedx` `externalReferences` for `dependency_name`
2. Calls `POST /ingest` with `entity_types: ["github_issues", "github_releases", "github_advisories"]`, `items: ["owner/repo"]`
3. Polls each job until done
4. Reads raw data from `github_issues_cache`, `github_releases_cache`, `github_advisories_cache` MongoDB collections
5. Applies existing LLM curation agents (`curators/issues.py`, `curators/releases.py`, `curators/vulnerabilities.py`) to the raw data
6. Maps to domain models and saves result

Note: workers do not fetch commits. The `commits` signal is dropped from `repo` under this design. The existing `GitHubMCPClient` and `repo_cache` collection are removed.

**`runtime`** already has `analyze` nodes with full logic (no workers integration needed — workers don't handle Docker execution). Both `repo` and `runtime` need:
- `constants.py` (ANALYZE constant)
- `graph.py` (StateGraph wrapping the analyze node, plus GRAPH_NAME, DEPENDS_ON, describe())
- `__init__.py` (exports: subgraph, GRAPH_NAME, DEPENDS_ON, describe)
- Registration in `ingestion_subgraphs/__init__.py`

### New nodes

**`risk_ranker`** — agentic node in `src/main_graph/nodes/risk_ranker.py`:
- Retrieves all Stage 1 artifacts from MongoDB via their DAOs
- Provides the LLM with a structured tool set: `get_vulnerabilities_result`, `get_registry_result`, `get_repo_result`, `get_runtime_result`
- LLM cross-analyzes signals across domains and produces a ranked list of deps with preliminary scores and rationale
- Returns `risk_rankings: list[dict]` (dep_name, score, rationale) and `high_risk_deps: list[str]`

**`risk_score`** — standard node in `src/main_graph/nodes/risk_score.py`:
- Reads `risk_rankings` (from risk_ranker) + `impact` artifacts (from Stage 2)
- Computes final composite score per dep: weighted average of risk signals + impact weight
- Returns `risk_scores: dict[str, float]`

**`recommendation`** — agentic node in `src/main_graph/nodes/recommendation.py`:
- LLM with tools: `search_npm_alternatives`, `get_package_info`, `compare_packages`
- For each high-risk dep: finds 1-3 maintained, compatible alternatives
- Returns `recommendations: list[dict]` (dep_name, risk_summary, alternatives)

### Modified components

**`execution_planner`** — updated to:
- Accept `Plan` object (subgraphs + dep_filter) instead of flat list
- Distinguish SBOM-level vs per-dep subgraphs
- Generate `(subgraph_name, dep_name)` pairs for per-dep fan-out
- Insert `risk_ranker` as an intermediate step between Stage 1 and Stage 2

**`execute_plan`** — updated to carry `dep_name` through to the subgraph invocation

**`planner.py`** — updated system prompt to:
- Present the six selectable capabilities with descriptions tied to concern types
- Produce a `Plan` object (subgraphs + dep_filter) instead of a flat list
- dep_filter extracted from user concern or change instructions

**`cross_analyzer`** — role narrows to: aggregate Stage 3 artifacts (risk_scores + recommendations) into a unified structured report. No longer the main analytical node.

---

## Workers Integration

The workers service (`http://localhost:8001`) handles npm and GitHub raw data fetching with rate limiting, retries, and MongoDB caching. `registry` and `repo` subgraphs use it instead of owning their own API clients.

**Interaction pattern (used by both `registry` and `repo`):**

```
1. POST /ingest  { entity_types, items }
   → returns { job_ids: { entity_type: job_id } }

2. Poll GET /status/{job_id}
   → { status: "pending"|"running"|"done"|"failed", total, completed, failed }
   → async sleep with backoff; max ~30s total wait

3. Read result from shared MongoDB collection directly
   (backend and workers share the same MongoDB instance)
   → npm_package_cache        keyed by package name
   → github_issues_cache      keyed by "owner/repo"
   → github_releases_cache    keyed by "owner/repo"
   → github_advisories_cache  keyed by "owner/repo"
```

**Coverage:**

| Entity | Workers entity_type | Collection | Used by |
|---|---|---|---|
| npm metadata | `npm` | `npm_package_cache` | `registry` |
| GitHub issues | `github_issues` | `github_issues_cache` | `repo` |
| GitHub releases | `github_releases` | `github_releases_cache` | `repo` |
| GitHub advisories | `github_advisories` | `github_advisories_cache` | `repo` |
| GitHub commits | — (not supported) | — | dropped from `repo` |

Workers provide **raw data only**. LLM curation of issues, releases, and advisories remains in the `repo` subgraph (existing `curators/` agents).

---

## Out of Scope

- Transitive dependency analysis (only direct deps are analyzed per-dep)
- Multi-language support (JavaScript/npm only)
- Workers integration (deferred)
- UI changes for the new pipeline stages
