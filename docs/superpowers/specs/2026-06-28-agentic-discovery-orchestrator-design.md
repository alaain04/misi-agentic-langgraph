# Agentic Discovery Orchestrator — Design Spec

**Date:** 2026-06-28
**Branch:** feat/superpower-investigation

---

## Problem

The current discovery subgraph is a rigid 5-node pipeline:

```
clone_repository → inspector_agent → lock_generator_agent → generate_sbom → build_dependency_summary
```

Each node is a fixed step. When a step fails (e.g. `npm ERESOLVE` peer dependency conflict, wrong Node version, missing lock file), the pipeline either skips to `build_dependency_summary` with an empty SBOM or applies a hardcoded fallback. It cannot observe the error output and adapt its approach.

---

## Goal

Replace the multi-node pipeline with a single ReAct orchestrator agent that has full visibility into errors and can retry with different strategies. The agent owns all discovery work: cloning, inspection, lock generation, and SBOM generation. `build_dependency_summary` stays as a separate downstream LLM node.

---

## New Graph Shape

```
START → discovery_orchestrator → build_dependency_summary → END
```

The discovery subgraph shrinks from 5 nodes to 2. The output contract to the main graph is unchanged.

---

## discovery_orchestrator Node

A single ReAct agent node implemented with `create_agent` (same pattern as `lock_generator_agent`). The node wrapper invokes the agent, then persists the SBOM result to MongoDB via `sbom_dao` after the agent returns.

### Tools

| Tool | Signature | Description |
|---|---|---|
| `clone_repo` | `(repo_url: str, job_id: str) → str` | Runs `alpine/git` in Docker, returns local path or raises |
| `list_dir` | `(path: str) → list[str]` | Lists files at a path |
| `read_file` | `(path: str) → str` | Reads file content |
| `write_file` | `(path: str, content: str) → str` | Writes or patches a file |
| `run_docker` | `(image: str, command: str, volume: str) → DockerResult` | Runs any container, returns `{returncode, stdout, stderr}` |

`clone_repo` is a convenience wrapper over `run_docker` for the alpine/git image. `run_docker` is the key tool — it gives the agent full control to try any npm/pnpm/yarn command, any flags, any Node image.

### Agent Strategy (System Prompt)

The agent is instructed to:

1. **Clone** the repository using `clone_repo`.
2. **Inspect** the repo: detect package manager from lock files and `package.json`, read `engines.node` and `packageManager` fields.
3. **Check for existing lock files first.** If a lock file is present (`pnpm-lock.yaml`, `yarn.lock`, `package-lock.json`), attempt SBOM generation immediately — no install step needed.
   - `pnpm-lock.yaml` → `pnpm sbom --sbom-format=cyclonedx`
   - `package-lock.json` → `npm sbom --sbom-format=cyclonedx --package-lock-only`
   - `yarn.lock` → generate `package-lock.json` with `npm install --package-lock-only`, then npm sbom
4. **If SBOM fails**, read the error and retry with adapted parameters:
   - `ERESOLVE` peer dependency conflict → retry with `--legacy-peer-deps`; if that fails, `--force`
   - Wrong Node version error → switch docker image (e.g. `node:20-alpine` → `node:22-alpine`)
   - pnpm command fails → fall back to npm in the same image
   - npm fails → try `--legacy-peer-deps`, then `--force`
5. **If no lock file**, generate one first using `run_docker` (same logic as current `lock_generator_agent`), verify it was created with `read_file`, then proceed to SBOM.
6. **Up to 8 attempts** total before giving up and returning a descriptive `sbom_error`.

### Structured Output

```python
class OrchestratorResult(BaseModel):
    repo_path: str
    detected_package_manager: str        # "npm" | "yarn" | "pnpm"
    package_manager_version: str         # e.g. "9.15.0" or "latest"
    manifest_files: list[str]
    docker_image: str                    # final image that succeeded
    sbom_cyclonedx: dict                 # empty dict if SBOM failed
    sbom_error: str | None               # set if SBOM could not be generated
    discovery_error: str | None          # set if clone or total failure
```

### Node Wrapper Responsibilities

After the agent returns `OrchestratorResult`, the node wrapper:
- Calls `sbom_dao.save(SbomEntry(...))` and stores `sbom_result_id` in state
- Writes all fields to `DiscoveryState`
- Keeps the agent pure (no DAO tools inside the agent)

---

## build_dependency_summary Node

Unchanged. Receives the same `DiscoveryState` fields as today and produces `discovery_summary`.

---

## DiscoveryState Changes

Remove fields that no longer need to be intermediate state (previously written by individual nodes that no longer exist):
- `lock_file_missing` — internal to the orchestrator, not exposed
- `lock_generation_attempts` — internal
- `lock_generation_error` — internal

Keep all output fields: `repo_path`, `manifest_files`, `detected_package_manager`, `package_manager_version`, `docker_image`, `sbom_cyclonedx`, `sbom_result_id`, `sbom_error`, `discovery_error`, `discovery_summary`, `project_metadata`.

---

## Files Affected

| File | Change |
|---|---|
| `subgraphs/discovery/graph.py` | Replace 5-node graph with 2-node graph |
| `subgraphs/discovery/nodes/discovery_orchestrator.py` | New — ReAct agent node |
| `subgraphs/discovery/tools/docker.py` | Add `clone_repo` tool; expose `run_docker` as LangChain tool |
| `subgraphs/discovery/state.py` | Remove lock-gen intermediate fields |
| `subgraphs/discovery/service.py` | Remove or simplify (sbom save logic moves to node wrapper) |
| `subgraphs/discovery/nodes/clone_repository.py` | Delete |
| `subgraphs/discovery/nodes/inspector_agent.py` | Delete |
| `subgraphs/discovery/nodes/lock_generator_agent.py` | Delete |
| `subgraphs/discovery/nodes/generate_sbom.py` | Delete |

---

## Recursion Limit

Set `recursion_limit=40` on the agent to allow up to ~8 tool-call cycles (each cycle = invoke + observe = ~5 steps).

---

## Success Criteria

- A repo with an existing lock file produces a valid SBOM without any `npm install` step.
- A repo with `ERESOLVE` peer conflicts succeeds after the agent retries with `--legacy-peer-deps`.
- A repo with no lock file generates one and then produces a valid SBOM.
- A total failure (e.g. private repo, corrupt package.json) sets `discovery_error` and allows the pipeline to continue gracefully to `build_dependency_summary`.
