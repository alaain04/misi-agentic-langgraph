# Graph Architecture

This document describes the LangGraph pipeline that processes analysis jobs.

## Overview

```
User Request (POST /analyze)
    ↓
[Job created in MongoDB, status=pending]
    ↓
Project Discovery Subgraph
    ↓
Planner Agent
    ↓
Task Dispatcher (parallel fan-out via Send)
    ├── Registry Subgraph
    ├── Repo Subgraph
    ├── Runtime Subgraph
    ├── Risk Score Subgraph
    └── Recommendation Subgraph
    ↓
Final Report
[Job updated in MongoDB, status=complete]
```

## Top-Level Graph (`graphs/main.py`)

The root `StateGraph` orchestrates the full pipeline. It picks up a pending job by `job_id`, runs all subgraphs, and writes the final report back to MongoDB.

**State fields:**

| Field | Type | Description |
|---|---|---|
| `job_id` | `str` | MongoDB ObjectId of the job |
| `repo_url` | `str` | Repository URL to analyze |
| `concern` | `str` | User-supplied analysis concern |
| `discovery` | `DiscoveryResult` | Output of the discovery subgraph |
| `plan` | `list[Task]` | Ordered task list from the planner |
| `subgraph_results` | `dict[str, Any]` | Keyed results from each subgraph |
| `report` | `str` | Final rendered report |

## Nodes

### `project_discovery` → `graphs/subgraphs/discovery.py`

Clones or fetches repo metadata, detects language/framework/dependencies, and produces a `DiscoveryResult` that all downstream nodes consume.

### `planner` → `graphs/nodes/planner.py`

An LLM agent that reads the `DiscoveryResult` and `concern`, then emits an ordered `list[Task]` describing which subgraphs to run and with what parameters.

### `task_dispatcher` → `graphs/nodes/dispatcher.py`

Fans out to subgraphs in parallel using LangGraph `Send`. Each `Task` in the plan becomes a `Send` targeting the appropriate subgraph node.

### Subgraphs (parallel branches)

Each subgraph is a self-contained `StateGraph` compiled and invoked as a node.

| Subgraph | File | Responsibility |
|---|---|---|
| **Registry** | `graphs/subgraphs/registry.py` | Checks package registries (npm, PyPI, etc.) for known vulnerabilities and outdated deps |
| **Repo** | `graphs/subgraphs/repo.py` | Static analysis of source code: secrets, misconfigs, code smells |
| **Runtime** | `graphs/subgraphs/runtime.py` | Infers runtime behaviour: exposed ports, env var usage, Dockerfile/k8s config |
| **Risk Score** | `graphs/subgraphs/risk_score.py` | Aggregates signals from other subgraphs into a structured risk score |
| **Recommendation** | `graphs/subgraphs/recommendation.py` | Generates prioritised, actionable remediation steps |

### `final_report` → `graphs/nodes/report.py`

Merges all `subgraph_results` into a structured Markdown report, persists it to the job document (`status=complete`, `result=<report>`), and returns the final state.

## File Layout

```
graphs/
├── __init__.py
├── main.py                    # Root StateGraph
├── nodes/
│   ├── planner.py
│   ├── dispatcher.py
│   └── report.py
└── subgraphs/
    ├── discovery.py
    ├── registry.py
    ├── repo.py
    ├── runtime.py
    ├── risk_score.py
    └── recommendation.py
```

## Key design decisions

- **Parallel fan-out via `Send`** — the dispatcher uses `Send` so all subgraphs run concurrently; the graph waits for all before proceeding to `final_report`.
- **Subgraphs as compiled graphs** — each subgraph is compiled independently (`subgraph.compile()`) and invoked as a node, keeping state schemas isolated.
- **Planner is optional** — if the concern maps directly to a fixed set of subgraphs, the dispatcher can skip the planner and fan out statically.
- **MongoDB as job store** — the graph reads input from and writes output to the `jobs` collection; it does not own HTTP concerns.
