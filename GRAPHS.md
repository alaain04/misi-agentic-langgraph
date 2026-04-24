# GRAPHS.md — LangGraph pipeline architecture

## Trigger flow

```
POST /analyze
  │
  ├─ creates Job (status=pending) in MongoDB
  ├─ returns 202 { trace_id, status: "pending" }
  │
  └─ asyncio.create_task(run_discovery(...))   ← fire-and-forget
       │
       ├─ JobDAO.update_status → running
       ├─ project_discovery_subgraph.ainvoke(...)
       └─ JobDAO.save_result | update_status(failed)

GET /analyze/{trace_id}
  └─ returns { trace_id, status }  ← client polls until done | failed
```

---

## ProjectDiscovery subgraph

**Location:** `src/graphs/project_discovery/`

**Purpose:** Clone-free inspection of a GitHub repository to identify JavaScript
package manager files, parse direct dependencies, and build an initial dependency
summary for downstream planner agents.

**Constraints:** deterministic only — no LLM calls, no external security APIs.

### Files

| File | Responsibility |
|---|---|
| `state.py` | `DiscoveryState` TypedDict, `ProjectMetadata`, `DependencyEntry` |
| `constants.py` | Node name string constants |
| `routes.py` | Routing functions (pure, importable, testable) |
| `graph.py` | `StateGraph` wiring + `build_project_discovery_subgraph()` |
| `nodes/fetch_repository.py` | Resolve GitHub URL, call REST API for repo metadata |
| `nodes/detect_manifest_files.py` | Walk repo tree via GitHub Trees API, filter manifest files |
| `nodes/parse_package_files.py` | Fetch + parse each manifest via GitHub Contents API |
| `nodes/build_dependency_summary.py` | Aggregate parsed data into final output fields |

### Graph topology

```
START
  └─► fetch_repository          (GitHub REST API → repo name, default branch)
        │
        ├─► [discovery_error] ──────────────────────► build_dependency_summary
        │                                                       │
        └─► detect_manifest_files                               │
              (GitHub Trees API → locate manifests)             │
                    │                                           │
                    └─► parse_package_files                     │
                          (GitHub Contents API → parse files)   │
                                │                               │
                                └─► build_dependency_summary ◄──┘
                                      (aggregate → final output)
                                            │
                                           END
```

### State

**Inputs**

| Field | Type | Description |
|---|---|---|
| `repo_url` | `str` | GitHub repository URL |
| `concern` | `str` | Analysis concern passed from the API |
| `token` | `str \| None` | Optional GitHub PAT for private repos / higher rate limits |

**Internal** (populated progressively by nodes)

| Field | Populated by |
|---|---|
| `repo_owner`, `repo_name`, `default_branch` | `fetch_repository` |
| `detected_files` | `detect_manifest_files` |
| `parsed_manifests` | `parse_package_files` |

**Outputs** (consumed by downstream agents)

| Field | Type | Description |
|---|---|---|
| `project_metadata` | `ProjectMetadata` | `name`, `package_manager`, `direct_dependencies_count` |
| `direct_dependencies` | `list[DependencyEntry]` | Each dep: `name`, `version_spec`, `is_dev` |
| `manifest_files` | `list[str]` | Relative paths of detected manifests |
| `discovery_summary` | `str` | Human-readable summary for the planner agent |
| `discovery_error` | `str \| None` | Set on fatal errors (bad URL, 404, rate limit) |

### Supported manifest formats

| File | Parser strategy |
|---|---|
| `package.json` | `json.loads` → `dependencies` + `devDependencies` |
| `package-lock.json` | `json.loads` → lockfile version + resolved package count |
| `yarn.lock` | Line-by-line regex — extracts package names from entry headers |
| `pnpm-lock.yaml` | Line-by-line — reads `dependencies:` / `devDependencies:` sections |

### Error handling

| Scenario | Behaviour |
|---|---|
| Bad / non-GitHub URL | `discovery_error` set in `fetch_repository`; short-circuits to summary |
| 404 (repo not found) | `discovery_error` set; short-circuits to summary |
| 403 / rate limit | `discovery_error` set; short-circuits to summary |
| No manifest files found | `detected_files = []`; summary reports zero dependencies |
| Malformed manifest | `parse_error` key recorded per file; other files still processed |
| Transient network error | `RetryPolicy(max_attempts=3, backoff_factor=2.0)` on all HTTP nodes |
