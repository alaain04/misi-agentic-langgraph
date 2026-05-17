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

**`impact`** — agentic subgraph. An LLM agent with filesystem and SBOM tools analyzes how one dependency is used inside the user's cloned project. See *Agentic Node Specifications* for full tool set.

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

**`risk_ranker`**, **`risk_score`**, and **`recommendation`** are all agentic. See *Agentic Node Specifications* for full tool sets and output schemas.

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

## Agentic Node Specifications

All four agentic nodes follow the same pattern: an LLM is bound to a set of typed tools, runs a ReAct loop, and produces a structured output. Tools are pure async functions decorated with `@tool`.

---

### `impact` subgraph (Stage 2)

**Goal:** Understand how a specific dependency is used inside the user's project and how many other packages would be affected if it changed.

**Tools:**

| Tool | Signature | What it does |
|---|---|---|
| `list_source_files` | `(repo_path: str, extensions: list[str] = [".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"]) -> list[str]` | Recursively lists all source files in the project, excluding node_modules |
| `find_usages` | `(dep_name: str, repo_path: str) -> list[dict]` | Greps for `import … from 'dep'`, `require('dep')`, and dynamic `import('dep')` patterns; returns `[{file, line, statement}]` |
| `read_file_excerpt` | `(path: str, around_line: int, context: int = 5) -> str` | Reads ±N lines around a usage site so the LLM can understand how the API is used |
| `get_direct_dependents` | `(dep_name: str, sbom: dict) -> list[str]` | Returns packages in `sbom.dependencies` that list `dep_name` as a direct dependency |
| `get_blast_radius` | `(dep_name: str, sbom: dict) -> dict` | Traverses the full SBOM dependency tree; returns `{direct_dependents: int, transitive_dependents: int, max_depth: int}` |

**System prompt direction:** The agent is instructed to: (1) find all usages of the dependency, (2) read a sample of usage sites to characterize how the API is used (e.g., "only the `get` method", "JSX rendering"), (3) compute the blast radius, (4) summarize the findings.

**Output saved to MongoDB:**
```python
class ImpactEntry(BaseModel):
    dep_name: str
    usage_count: int
    affected_files: list[str]
    api_surface_used: list[str]       # e.g. ["useState", "useEffect"]
    usage_summary: str                # LLM narrative
    direct_dependents: int
    transitive_dependents: int
    max_depth: int
    blast_radius_summary: str         # LLM narrative
```

---

### `risk_ranker` (between Stage 1 and Stage 2)

**Goal:** Cross-analyze all Stage 1 signals for every dep in scope, produce a preliminary risk ranking, and select the high-risk subset for Stage 2 impact analysis.

**Tools:**

| Tool | Signature | What it does |
|---|---|---|
| `list_analyzed_deps` | `() -> list[str]` | Returns all dep names that have Stage 1 results available |
| `get_vulnerabilities` | `(dep_name: str) -> dict` | Retrieves CVE findings from `vulnerabilities_dao` |
| `get_license_compliance` | `(dep_name: str) -> dict` | Retrieves license analysis from `license_compliance_dao` |
| `get_registry_data` | `(dep_name: str) -> dict` | Retrieves npm health from `registry_dao` |
| `get_repo_data` | `(dep_name: str) -> dict` | Retrieves GitHub signals from `repo_dao` |
| `get_runtime_data` | `(dep_name: str) -> dict` | Retrieves test/lint results from `runtime_dao` |
| `save_ranking` | `(rankings: list[dict]) -> None` | Persists the ranked list to state; each entry: `{dep_name, preliminary_score, risk_signals: list[str], rationale: str}` |

**System prompt direction:** The agent is instructed to: (1) retrieve signals for all analyzed deps, (2) reason about cross-domain risk (e.g., a deprecated package with CVEs is higher risk than one with only CVEs), (3) produce an ordered ranking with rationale per dep, (4) flag the high-risk subset using the selection criteria in the *risk_ranker selection threshold* section.

**Output written to `MainState`:**
```python
risk_rankings: list[dict]    # [{dep_name, preliminary_score, risk_signals, rationale}]
high_risk_deps: list[str]    # deps selected for Stage 2
```

---

### `risk_score` (Stage 3)

**Goal:** Compute the final authoritative risk score per dependency, incorporating both Stage 1 risk signals and Stage 2 impact findings.

**Tools:**

| Tool | Signature | What it does |
|---|---|---|
| `get_preliminary_ranking` | `(dep_name: str) -> dict` | Retrieves the risk_ranker assessment for this dep |
| `get_impact_data` | `(dep_name: str) -> dict \| None` | Retrieves Stage 2 impact analysis (None if dep was not high-risk / impact not selected) |
| `get_vulnerabilities` | `(dep_name: str) -> dict` | CVE findings |
| `get_registry_data` | `(dep_name: str) -> dict` | npm health |
| `get_repo_data` | `(dep_name: str) -> dict` | GitHub signals |
| `get_runtime_data` | `(dep_name: str) -> dict` | Test/lint results |
| `save_risk_score` | `(dep_name: str, score: float, severity: str, breakdown: dict, rationale: str) -> None` | Persists the final score |

**System prompt direction:** The agent produces a 0–10 score per dep, a severity label (`critical / high / medium / low`), a per-dimension breakdown, and a human-readable rationale. It must call `save_risk_score` for every dep in scope.

**Output saved to MongoDB per dep:**
```python
class RiskScoreEntry(BaseModel):
    dep_name: str
    score: float                    # 0–10
    severity: str                   # critical | high | medium | low
    breakdown: dict[str, float]     # {vulnerabilities: 8.5, maintenance: 3.0, ...}
    rationale: str
    impact_weight: float | None     # None if impact not analyzed
```

---

### `recommendation` (Stage 3)

**Goal:** For each high-risk dependency, find 1–3 actively maintained alternatives and explain the migration trade-off.

**Tools:**

| Tool | Signature | What it does |
|---|---|---|
| `get_risk_score` | `(dep_name: str) -> dict` | Retrieves the final risk score and breakdown |
| `get_impact_data` | `(dep_name: str) -> dict \| None` | Retrieves usage + blast radius for context |
| `search_npm` | `(query: str, max_results: int = 10) -> list[dict]` | Searches the npm registry; returns `[{name, description, weekly_downloads, last_publish}]` |
| `get_npm_metadata` | `(package_name: str) -> dict` | Full npm metadata: version history, maintainers, homepage, repository, license, deprecation |
| `get_github_summary` | `(owner: str, repo: str) -> dict` | GitHub stars, open issues, last commit date, release frequency |
| `compare_packages` | `(original: str, alternatives: list[str]) -> dict` | Side-by-side comparison of download trends, maintenance health, and license |
| `save_recommendation` | `(dep_name: str, risk_summary: str, alternatives: list[dict], migration_notes: str) -> None` | Persists the recommendation |

**System prompt direction:** The agent is instructed to: (1) understand the risk profile and current usage of the dep, (2) search for alternatives in the same problem space, (3) evaluate each candidate on maintenance health, downloads, license compatibility, and API similarity, (4) select the top 1–3 and write a migration trade-off note, (5) call `save_recommendation` for each high-risk dep.

**Output saved to MongoDB per dep:**
```python
class RecommendationEntry(BaseModel):
    dep_name: str
    risk_summary: str
    alternatives: list[AlternativeEntry]
    migration_notes: str

class AlternativeEntry(BaseModel):
    name: str
    reason: str                      # why this is a good alternative
    weekly_downloads: int | None
    last_publish: str | None
    license: str | None
    api_similarity: str              # high | medium | low
    migration_effort: str            # low | medium | high
```

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
