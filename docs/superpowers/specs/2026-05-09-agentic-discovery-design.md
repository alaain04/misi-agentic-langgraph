# Agentic Discovery Design

**Date:** 2026-05-09  
**Scope:** `backend/src/main_graph/subgraphs/discovery/`  
**Domain:** Node.js repositories only (npm / yarn / pnpm)

---

## Goal

Replace the current static discovery pipeline with a fully agentic system that:
- Uses a main inspector agent to read and reason about manifest contents
- Delegates lock file generation to a dedicated lock generator agent when the lock file is missing
- Iterates intelligently on install errors (up to 6 attempts) using LLM reasoning per attempt
- Produces structured outputs: job metadata, raw CycloneDX SBOM, and a concise discovery summary

---

## Graph topology

```
START
  → clone_repository              (deterministic — git clone via Docker alpine/git)
      [clone error] ──────────────────────────────────────────────────► build_dependency_summary
      [success] ↓
  → inspector_agent               (ReAct — reads manifests, detects PM + lock file)
      [no manifest / agent error] ────────────────────────────────────► build_dependency_summary
      [lock_file_missing = true]  ↓
  → lock_generator_agent          (ReAct — install, read errors, fix, ≤6 iters)
      ↓ (success or exhausted)
  → generate_sbom                 (deterministic — Trivy CycloneDX)
      ↑
      [lock_file_missing = false] (skip lock generator entirely)
  → build_dependency_summary      (deterministic — extract metadata + LLM summary)
  → END
```

`clone_repository` retains the existing Docker + `alpine/git` shallow-clone logic unchanged. The current `fetch_repository` LLM install logic is removed entirely and replaced by the two agents.

---

## Agents

### inspector_agent

**Type:** `create_react_agent`  
**Model:** GPT-4o-mini  
**Tools:**
- `list_dir(path: str) -> list[str]` — lists a directory in the cloned workspace
- `read_file(path: str) -> str` — reads a file from the workspace

**Behaviour:** Lists the repo root, reads `package.json` and any lock files present. Determines which of npm / yarn / pnpm is in use by lock file presence (pnpm > yarn > npm priority). Reads `engines.node` from `package.json` to select the correct Node Docker image tag. Detects whether a lock file exists.

**Structured response:**
```python
class InspectorResult(BaseModel):
    detected_package_manager: str  # "npm" | "yarn" | "pnpm"
    lock_file_missing: bool
    manifest_files: list[str]      # e.g. ["package.json"]
    docker_image: str              # e.g. "node:22-alpine"
    install_command: str           # e.g. "npm install"
```

**State updates:** `detected_package_manager`, `lock_file_missing`, `manifest_files`, `docker_image`, `install_command`

---

### lock_generator_agent

**Type:** `create_react_agent`  
**Model:** GPT-4o-mini  
**Max iterations:** 6  
**Tools:**
- `run_docker_command(image: str, command: str) -> CommandResult(returncode, stdout, stderr)`
- `read_file(path: str) -> str` — read manifest or error context
- `write_file(path: str, content: str)` — patch `package.json` if needed (workspace is a temp dir, safe to modify)

**Context injection:** Before invoking, the node builds a system prompt dynamically from state fields: `repo_path`, `detected_package_manager`, `docker_image`, `install_command`.

**Behaviour:** Runs the install command in Docker. On failure, reads stdout/stderr, reasons about the error class (peer conflict, version range mismatch, missing engine, network error), applies a fix (different flags such as `--legacy-peer-deps`, patched `package.json` version range, different Node image), and retries. `write_file` is constrained to paths within `repo_path` only. Stops when the expected lock file exists on disk or when 6 tool-call cycles are exhausted (`recursion_limit=13` on agent invocation).

**Structured response:**
```python
class LockGenResult(BaseModel):
    success: bool
    attempts: int
    error: str | None  # last error message if not successful
```

**State updates:** `lock_generation_attempts`, `lock_generation_error`

---

## State schema

```python
class DiscoveryState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str

    # set by clone_repository
    repo_path: NotRequired[str]

    # set by inspector_agent
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]   # "npm" | "yarn" | "pnpm"
    lock_file_missing: NotRequired[bool]
    docker_image: NotRequired[str]               # e.g. "node:22-alpine"
    install_command: NotRequired[str]            # e.g. "npm install"

    # set by lock_generator_agent
    lock_generation_attempts: NotRequired[int]
    lock_generation_error: NotRequired[str | None]

    # set by generate_sbom
    sbom_cyclonedx: NotRequired[dict]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # outputs
    project_metadata: NotRequired[ProjectMetadata]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
```

`ProjectMetadata` retains its current shape: `name`, `package_manager`, `direct_dependencies_count`.

---

## Discovery summary

`build_dependency_summary` produces a 3-paragraph plain prose text (~150 words):

1. **Ecosystem profile** — project name, package manager, direct + transitive counts, notable packages
2. **Concern relevance** — which packages are most relevant to the user's concern and why
3. **Risk signals** — dependency profile flags visible from the SBOM (pinned vs. range versions, deep transitive tree, dev deps in prod, etc.)

No headers, no lists — plain paragraphs. Concise and direct.

---

## Error handling

| Stage | Failure | Behavior |
|---|---|---|
| `clone_repository` | git clone fails (bad URL, 404, timeout) | set `discovery_error`, skip to `build_dependency_summary` |
| `inspector_agent` | agent errors or no manifest found | set `discovery_error`, skip to `build_dependency_summary` |
| `lock_generator_agent` | exhausts 6 iterations | set `lock_generation_error` (non-fatal), continue to `generate_sbom` |
| `generate_sbom` | Trivy scan fails | set `sbom_error`, summary reports partial/empty SBOM |
| `build_dependency_summary` | errors present in state | produces minimal metadata + summary describing what failed |

Lock generation failure is non-fatal. Trivy can still produce a partial SBOM from `package.json` alone (direct deps only). The summary will note that transitive dependencies may be incomplete.

---

## Files affected

| File | Change |
|---|---|
| `nodes/fetch_repository.py` | Rename to `clone_repository.py`; strip install logic and LLM call |
| `nodes/inspector_agent.py` | New — `create_react_agent` with `list_dir` + `read_file` tools |
| `nodes/lock_generator_agent.py` | New — `create_react_agent` with `run_docker_command` + `read_file` + `write_file` tools |
| `nodes/generate_sbom.py` | Unchanged |
| `nodes/build_dependency_summary.py` | Minor: handle `lock_generation_error` in prompt context |
| `state.py` | Add new fields from inspector and lock generator |
| `graph.py` | Rewire topology: 5 nodes, new conditional routing |
| `constants.py` | Add new node name constants |
