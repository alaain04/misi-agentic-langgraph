# ReAct Conductor Design

**Date:** 2026-07-05
**Status:** Approved

## Overview

Replace the current rigid pipeline (planner → dispatcher → executor → correlator → reviewer) with a ReAct conductor loop. The conductor is an LLM agent that reasons, calls tools in parallel, observes results, and decides when to ask the user or finalize — all within a tight graph loop.

The frontend HITL contract (`/chat` endpoint, `interrupt()` mechanism, artifact shape) stays unchanged.

---

## Architecture

The 8-node pipeline collapses to 5 nodes:

```
START → prep → conductor ⟷ tool_runner → hitl_gate → report_builder → END
                  ↑__________________________|
```

**Removed nodes:** `investigation_planner`, `skill_dispatcher`, `skill_executor`, `evidence_collector`, `evidence_correlator`, `finding_reviewer`

**New nodes:** `conductor`, `tool_runner`, `hitl_gate`

**Kept nodes:** `prep` (renamed from `discovery`), `report_builder`

### Graph loop

```
prep → conductor
conductor → tool_runner         (when tool_calls non-empty)
conductor → hitl_gate           (when ask_user or checkpoint_message set)
conductor → report_builder      (when finalize=true)
tool_runner → conductor         (always, loops back)
hitl_gate → conductor           (after user responds or autopilot pass-through)
report_builder → END
```

Max iterations guard: if `conductor_iteration >= 10`, force `finalize=true`. The conductor node increments `conductor_iteration` at the start of each invocation before calling the LLM.

---

## Prep Phase

Renamed from `discovery`. Logic largely unchanged:

1. `clone_repo` — Docker `alpine/git`, always runs
2. `inspect_repo` — detect lock file, package manager (ReAct agent with `list_dir`, `read_file`)
3. `install_deps` — only if no lock file detected
4. `build_project_context` — lightweight LLM call producing `project_context: str` (replaces `build_dependency_summary`)

**Removed from prep:** `generate_sbom` (CycloneDX SBOM is no longer a hard requirement; the conductor queries dependency data on demand via tools).

**Prep output written to state:** `repo_path`, `project_metadata`, `manifest_files`, `detected_package_manager`, `project_context`, `discovery_error`.

---

## Conductor

Single LangGraph node. Each iteration calls the LLM with structured output.

### Input context (system + user prompt)

- User concern
- `project_context` from prep
- Accumulated `tool_results` (all prior tool outputs)
- Accumulated `findings` (structured notes written in prior iterations)
- Conversation `messages` (HITL history)
- Available tools with descriptions and argument schemas
- `autopilot` flag — if true, instruct the LLM to never emit `ask_user` or `checkpoint_message`

### Output: `ConductorDecision`

```python
class ToolCall(BaseModel):
    tool: str        # tool name, e.g. "npm_audit"
    args: dict       # tool-specific arguments
    reason: str      # shown in artifact tracking

class FindingNote(BaseModel):
    dep_name: str
    severity: str    # "critical" | "high" | "medium" | "low" | "info"
    description: str
    evidence_refs: list[str]   # references to tool_result ids

class ConductorDecision(BaseModel):
    tool_calls: list[ToolCall]        # empty = no tools this iteration
    findings: list[FindingNote]       # new findings synthesized from tool results
    ask_user: str | None              # question to pause on (None in autopilot)
    checkpoint_message: str | None    # summary to validate before continuing (None in autopilot)
    finalize: bool                    # ready to build report
    reasoning: str                    # internal chain-of-thought, stored but not shown to user

# Decision fields are mutually exclusive by priority:
#   finalize=true → ignore tool_calls and ask_user/checkpoint_message
#   ask_user/checkpoint_message set → ignore tool_calls (HITL happens first)
#   tool_calls non-empty → normal execution path
```

The conductor writes `FindingNote` entries as it processes tool results. These accumulate in state across iterations via `operator.add` and become the report builder's primary input.

---

## Tool Runner

Receives `tool_calls` from the conductor decision. Executes all requested tools concurrently via `asyncio.gather`. Returns a list of `ToolResult` entries appended to state.

```python
class ToolResult(BaseModel):
    id: str          # uuid, referenced by FindingNote.evidence_refs
    tool: str
    args: dict
    output: dict     # structured JSON from the tool
    error: str | None
    duration_ms: int
```

Tool failures are non-fatal: `ToolResult.error` is set and the conductor observes it on the next iteration, deciding whether to retry or move on.

---

## Tools

All tools are async. External API calls have a 10s timeout. Failures return a structured error object.

### Dependency analysis
| Tool | Description |
|------|-------------|
| `npm_audit(repo_path)` | Runs `npm audit --json`; returns vulnerabilities with severity, CVE IDs, affected packages |
| `npm_list(repo_path)` | Runs `npm list --json`; returns full dependency tree with versions |
| `npm_outdated(repo_path)` | Returns packages with newer versions available |
| `check_licenses(repo_path)` | Parses license info from `npm list --json --long`; flags problematic licenses |

### Registry & advisory lookups
| Tool | Description |
|------|-------------|
| `npm_registry_info(package_name)` | Fetches publish history, maintainers, download counts from npm registry API |
| `github_advisory(package_name, ecosystem)` | Queries GitHub Advisory Database (GraphQL) for known vulnerabilities |
| `osv_lookup(package_name, version, ecosystem)` | Queries OSV.dev for vulnerability records |

### Repository inspection
| Tool | Description |
|------|-------------|
| `read_file(repo_path, relative_path)` | Reads a file from the cloned repo |
| `list_directory(repo_path, relative_path)` | Lists files at a path in the cloned repo |

### Package health
| Tool | Description |
|------|-------------|
| `github_repo_info(owner, repo)` | Stars, last commit date, open issues, archived status from GitHub API |

Docker-based Trivy scanner is removed. Coverage equivalent is provided by `npm_audit` + `github_advisory` + `osv_lookup`.

---

## State Changes

### Removed fields
```
investigation_plan, evidence, current_skill_id, current_dep_name,
current_hypothesis_id, risk_findings, contradictions,
reviewer_feedback, review_approved, review_iterations,
executed_skill_tasks, sbom_cyclonedx, sbom_result_id,
sbom_error, lock_generation_error, discovery_summary
```

### New MainState
```python
class MainState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str
    autopilot: bool                                           # new, default False

    # Prep outputs
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    project_context: NotRequired[str]                         # replaces discovery_summary
    discovery_error: NotRequired[str | None]

    # Conductor loop
    tool_results: Annotated[list[ToolResult], operator.add]   # all tool outputs
    findings: Annotated[list[FindingNote], operator.add]      # accumulated findings
    conductor_iteration: NotRequired[int]                     # loop counter
    messages: Annotated[list, add_messages]                   # HITL conversation

    # Output
    analysis_report: NotRequired[dict]
    cancelled: NotRequired[bool]
```

---

## HITL and Autopilot

The `/chat` API endpoint and `interrupt()` mechanism are unchanged. The frontend does not need to know the internal loop changed.

### `hitl_gate` node

Reads the latest conductor decision from state:

- `ask_user` set + not autopilot → `interrupt()`, resume with user reply appended to `messages`
- `checkpoint_message` set + not autopilot → `interrupt()`, user can approve or redirect concern
- autopilot active → pass through without interrupting in both cases

### Concern chaining

When the user sends a new concern mid-investigation (e.g. "actually focus on supply chain"), the resumed message lands in `messages`. The conductor sees it on the next iteration and pivots naturally — no special routing required.

### Autopilot

Passed as `autopilot: bool` in the initial job request. The conductor's system prompt instructs it to run to completion without pausing. The `hitl_gate` node becomes a no-op pass-through.

---

## Report Builder

Unchanged role. Updated inputs: receives `findings: list[FindingNote]` and `messages` instead of `risk_findings` and `evidence`. Single LLM call that formats the accumulated findings into the final report document. No agent loop, no additional tool access — the conductor has already done the reasoning.

---

## What Does Not Change

- `/chat` API contract and `interrupt()` mechanism
- Artifact shape for the frontend (node name changes, but the `messages` + `data` structure is the same)
- `report_builder` output shape (`analysis_report`)
- Prep phase logic (clone, inspect, optional install)
- `InMemorySaver` checkpointer

---

## Removed

- All `InvestigationSkill` classes and the `skills/` package
- `skill_dispatcher`, `skill_executor`, `evidence_collector`, `evidence_correlator`, `finding_reviewer` nodes
- CycloneDX SBOM generation (`generate_sbom` node, Syft/npm sbom tooling)
- Docker-based Trivy scanner
- Vector store integration (was unused in practice)
