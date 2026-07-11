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

All tools are async. Tools that hit external APIs (marked ★) have a 10s timeout, require rate limiting, and should cache results within a session. Failures return a structured error object the conductor can observe and decide whether to retry or skip.

### Core dependency inventory
| Tool | Description |
|------|-------------|
| `npm_list(repo_path)` | Runs `npm list --json`; returns full dependency tree with versions |
| `package_json(repo_path)` | Parses `package.json`; returns declared dependencies, scripts, engines, package manager, workspaces |
| `package_lock(repo_path)` | Parses `package-lock.json` (or equivalent lockfile); returns resolved versions and integrity hashes |
| `npm_outdated(repo_path)` | Returns packages with newer versions available |

### Security
| Tool | Description |
|------|-------------|
| `npm_audit(repo_path)` | Runs `npm audit --json`; returns vulnerabilities, severities, advisories, affected packages |
| `dependency_confusion(repo_path)` | Detects internal/private package names that may be vulnerable to dependency confusion attacks |
| `install_scripts(repo_path)` | Detects packages with `preinstall`, `install`, `postinstall`, or other lifecycle scripts |

### External advisories ★
| Tool | Description |
|------|-------------|
| `github_advisory(package_name, ecosystem)` | Queries GitHub Advisory Database (GraphQL) for known vulnerabilities |
| `osv_lookup(package_name, version, ecosystem)` | Queries OSV.dev for vulnerability records |

### Licensing
| Tool | Description |
|------|-------------|
| `check_licenses(repo_path)` | Collects licenses for all dependencies and flags disallowed licenses |

### Dependency health
| Tool | Description |
|------|-------------|
| `duplicate_packages(repo_path)` | Finds multiple installed versions of the same package |
| `missing_dependencies(repo_path)` | Finds imported packages missing from `package.json` |
| `dependency_size(repo_path)` | Estimates install size and identifies large dependencies |
| `dependency_stats(repo_path)` | Reports total, direct, transitive, dev, optional, and peer dependency counts |

### Version analysis
| Tool | Description |
|------|-------------|
| `version_ranges(repo_path)` | Detects broad version ranges (`*`, `latest`, wide `^`/`>=`) |
| `deprecated_packages(repo_path)` | Detects deprecated packages using npm metadata |
| `breaking_updates(repo_path)` | Identifies available major-version upgrades |

### Supply-chain risk ★
| Tool | Description |
|------|-------------|
| `package_reputation(package_name)` | Reports package age, maintainers, release cadence, popularity via npm registry + GitHub API |
| `unmaintained_packages(repo_path)` | Flags packages with no releases or commits for a long period |
| `typosquat_detection(repo_path)` | Detects package names similar to popular packages |
| `high_risk_packages(repo_path)` | Flags packages with unusual characteristics (single maintainer, very new, abandoned, etc.) |

### Ad-hoc repository inspection
| Tool | Description |
|------|-------------|
| `read_file(repo_path, relative_path)` | Reads a specific file from the cloned repo |
| `list_directory(repo_path, relative_path)` | Lists files at a path in the cloned repo |

### Monorepo
| Tool | Description |
|------|-------------|
| `workspace_dependencies(repo_path)` | Lists dependencies by workspace/package for monorepo projects |

**Removed from original design:** Docker-based Trivy scanner. Coverage is provided by `npm_audit` + `github_advisory` + `osv_lookup`.

**Not included (deferred):** `npm_sbom` (redundant with `npm_list`), `license_notices` (report generation, not investigation), `unused_dependencies` (noisy AST analysis), `dependency_graph` (prohibitively large output), `reachability_check` (complex static analysis — good follow-up iteration).

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
