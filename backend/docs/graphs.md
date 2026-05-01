# Graph Architecture

See [architecture.md](architecture.md) for the high-level system overview and request lifecycle.

---

## Implemented: Discovery subgraph

**Location:** `src/graphs/discovery/`

**Purpose:** Clone-free inspection of a GitHub repository to identify JavaScript package manager files, parse direct dependencies, and build an initial dependency summary for downstream planner agents.

### Files

| File | Responsibility |
|---|---|
| `state.py` | `DiscoveryState` TypedDict, `ProjectMetadata`, `DependencyEntry` |
| `constants.py` | Node name string constants |
| `routes.py` | Routing functions (pure, importable, testable) |
| `graph.py` | `StateGraph` wiring + `build_discovery_subgraph()` |
| `nodes/fetch_repository.py` | Resolve GitHub URL → REST API for repo metadata |
| `nodes/detect_manifest_files.py` | Walk repo tree via GitHub Trees API, filter manifest files |
| `nodes/parse_package_files.py` | Fetch + parse each manifest via GitHub Contents API |
| `nodes/build_dependency_summary.py` | Aggregate parsed data, call LLM, produce final output fields |

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
                                      (LLM → final output)
                                            │
                                           END
```

### State schema

**Inputs**

| Field | Type | Description |
|---|---|---|
| `repo_url` | `str` | GitHub repository URL |
| `concern` | `str` | Analysis concern passed from the API |
| `token` | `str \| None` | Optional GitHub PAT for private repos or higher rate limits |

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

Package manager is detected by lock file presence: pnpm > yarn > npm (priority order).

### Error handling

| Scenario | Behaviour |
|---|---|
| Bad / non-GitHub URL | `discovery_error` set in `fetch_repository`; short-circuits to summary |
| 404 (repo not found) | `discovery_error` set; short-circuits to summary |
| 403 / rate limit | `discovery_error` set; short-circuits to summary |
| No manifest files found | `detected_files = []`; summary reports zero dependencies |
| Malformed manifest | `parse_error` key recorded per file; other files still processed |
| Transient network error | `RetryPolicy(max_attempts=3, backoff_factor=2.0)` on all HTTP nodes |

---

## Planned: full pipeline

The root `StateGraph` (not yet implemented) orchestrates the complete analysis. It picks up a pending job by `job_id`, runs all subgraphs, and writes the final report back to MongoDB.

### Top-level graph topology

```
START
  └─► discovery
        └─► planner              (LLM: reads DiscoveryResult + concern → list[Task])
              └─► task_dispatcher (Send fan-out: one Send per Task)
                    ├─► registry_subgraph
                    ├─► repo_subgraph
                    ├─► runtime_subgraph
                    ├─► risk_score_subgraph
                    └─► recommendation_subgraph
                          └─► final_report
                                └─► END  (MongoDB: status=complete, result=<report>)
```

### Top-level state fields

| Field | Type | Description |
|---|---|---|
| `job_id` | `str` | MongoDB ObjectId of the job |
| `repo_url` | `str` | Repository URL to analyze |
| `concern` | `str` | User-supplied analysis concern |
| `discovery` | `DiscoveryResult` | Output of the discovery subgraph |
| `plan` | `list[Task]` | Ordered task list from the planner |
| `subgraph_results` | `dict[str, Any]` | Keyed results from each parallel subgraph |
| `report` | `str` | Final rendered report |

### Planned nodes

| Node | File (planned) | Responsibility |
|---|---|---|
| `planner` | `graphs/nodes/planner.py` | LLM reads `DiscoveryResult` + `concern`, emits `list[Task]` |
| `task_dispatcher` | `graphs/nodes/dispatcher.py` | `Send` fan-out to subgraphs in parallel |
| `registry_subgraph` | `graphs/subgraphs/registry.py` | npm/PyPI vulnerability checks, outdated deps |
| `repo_subgraph` | `graphs/subgraphs/repo.py` | Static analysis: secrets, misconfigs, code smells |
| `runtime_subgraph` | `graphs/subgraphs/runtime.py` | Dockerfile/k8s config, env var usage, exposed ports |
| `final_report` | `graphs/nodes/report.py` | Merges results → Markdown report → MongoDB |

### Planned file layout

```
src/graphs/
├── __init__.py
├── main.py                        # Root StateGraph
├── nodes/
│   ├── planner.py
│   ├── dispatcher.py
│   └── report.py
├── subgraphs/
│   ├── registry.py
│   ├── repo.py
│   ├── runtime.py
└── discovery/                     # Implemented ✓
    └── ...
```

### Key design decisions (planned)

- **Parallel fan-out via `Send`** — the dispatcher uses `Send` so all subgraphs run concurrently; the graph waits for all before proceeding to `final_report`.
- **Subgraphs as compiled graphs** — each subgraph is compiled independently (`subgraph.compile()`) and invoked as a node, keeping state schemas isolated.
- **Planner is optional** — if the concern maps directly to a fixed set of subgraphs, the dispatcher can skip the planner and fan out statically.
- **MongoDB as job store** — the graph reads input from and writes output to the `jobs` collection; HTTP concerns belong to the API layer, not the graph.
