# Spec: Remediation Planner Decomposition — Investigate → Plan → Dispatch

**Date:** 2026-08-02
**Scope:** Backend only (`apps/backend`), remediation subgraph
(`src/main_graph/subgraphs/remediation/`). Decomposes today's monolithic
per-target remediation sub-agent into an explicit investigate → plan →
dispatch pipeline, with a persisted, reviewable migration plan.
**Status:** Spec A of a two-spec split. Spec B (real dependency
replacement / r3) is a follow-up; see "Phasing" below.

## Context

Today the remediation subgraph is four nodes
(`classify_targets_node → root_deepagent_node → group_and_verify_gate →
pr_and_persist_node`, `remediation/graph.py`). All of *investigate,
decide-strategy, edit-code, self-verify* is fused into one prompt inside a
single per-target deep agent (`subagent_wrapper.py::_run`'s nested
`create_deep_agent`, driven by `_SYSTEM_PROMPT` steps 1–6). Consequences the
current design carries:

- **No separation of concerns.** Investigation tools (`dependents_of`,
  `blast_radius`, `search_code`, `read_release_notes`) are things the agent
  *may* call at its discretion — nothing guarantees the evidence was ever
  gathered.
- **No reviewable plan.** `strategy` and `migration_plan` fall out of the
  agent as a byproduct; there is no explicit, persisted artifact describing
  what the remediation intends to do, per target, for later review.
- **r3 (dependency replacement) is stubbed.** `classify_targets_node`
  settles every r3 target as `skipped` ("dependency migration — deferred,
  not yet supported", `classify.py:145`) and `group_and_verify_gate` defers
  any group coupled to a `replace` target.
- **Release-notes investigation is shallow.** `fetch_release_notes`
  (`changelog.py`) pulls the *last 20 releases* regardless of version and
  dumps raw truncated bodies; it is not scoped to the installed→target
  window and does no breaking-change extraction.

The deterministic backstop — `group_and_verify_gate` /
`replay_and_verify_group` (fresh-clone replay + install/build/test/audit,
never trusting agent self-report) and `pr_and_persist_node` — is the mature
part of the subgraph and is **retained unchanged**. This spec reshapes only
the front half (selection → plan → dispatch).

## Goals

1. Replace the monolithic per-target sub-agent with an explicit
   **investigate → plan → dispatch** pipeline, so each stage has one job and
   is independently observable/testable.
2. Make the migration plan a **first-class, persisted, reviewable artifact**
   (`MigrationPlan`), not an agent byproduct.
3. Guarantee investigation always runs (deterministic phase), instead of
   leaving it to agent discretion.
4. Keep the deepagent where it earns its place — planning + delegation, and
   the code-editing agents — and drop it where the work is deterministic or a
   single structured call.
5. Keep bump *execution* deterministic (the LLM plans; `apply_bump` runs the
   bump), and keep one uniform planning path across all tiers.

## Non-goals (Spec A)

- **Real dependency replacement (r3).** The `replace` task *kind* and its
  routing exist, but the replacement-migrator keeps today's behavior:
  `replace` groups settle as deferred/skipped. Full implementation is Spec B.
- **HITL plan-review gate.** Consent stays where it is today (at PR
  creation, `pr_and_persist_node`). The plan is persisted for review, but no
  interactive approval gate is added.
- **Failure-log-informed repair loop.** The correction-round retry keeps
  today's behavior (re-dispatch failed targets, unchanged bounds). Feeding
  verification logs back into repair is a separate improvement.
- **Locus-level task granularity.** Tasks are typed by change-kind
  (bump/codemod/replace), not split per-file or per-call-site.
- Any change to the analysis subgraph. (The version-enrichment spec is a
  *soft dependency* — see below — but is out of scope here.)

## Soft dependency: analysis-finding version enrichment

The Release investigator's changelog window is bounded by a **target
version**. The natural source is the finding's `fixed_version`, surfaced by
`2026-08-02-analysis-finding-version-enrichment.md`. Spec A does **not**
depend on that spec landing first: when `fixed_version` is absent, the
Release investigator falls back to the latest version satisfying the
declared range (else latest stable). Once version-enrichment lands, the
window sharpens automatically. No hard ordering between the two.

## Decisions

- **D1 — Tier becomes an advisory hint, not a routing gate.**
  `classify_targets_node` keeps classifying r1/r2/r3 from release notes but
  no longer *settles* r3 as skipped. Every selected target flows to
  investigation and planning; the tier is passed through as a hint the
  planner reads alongside real evidence. The r3-settle branch in
  `classify.py` is removed.

- **D2 — Investigation is a guaranteed deterministic phase
  (`investigate_node`), fanned out per target with `asyncio.gather`.** It
  produces one `TargetInvestigation` per target:
  - **Dependency investigator (deterministic):** `dependents_of` +
    `blast_radius` over `prep.dependency_graph`. No LLM.
  - **Source investigator (deterministic):** `search_code` for the target's
    call sites. No LLM.
  - **Release investigator (deterministic fetch + one LLM digest):**
    version-scoped — resolve current installed version and target version,
    fetch *every* changelog/release entry with a tag in the
    `(current, target]` window (filter the releases list by semver; `gh api`
    as today, optionally `CHANGELOG.md`), then a single
    `with_structured_output` call digests them into a `ReleaseDigest`
    (`migration_needed`, `migration_guide` prose only if warranted, itemized
    `breaking_changes`). This is a structured call, **not** an agent loop.

- **D3 — Planning and dispatch are one deepagent
  (`plan_and_orchestrate`, the reshaped `root_deepagent_node`), but the plan
  is a mandatory structured artifact.** Per target the deepagent:
  1. reads the target's `TargetInvestigation` + tier hint,
  2. **emits a structured `MigrationPlan` before dispatching anything** —
     enforced via a mandatory `commit_plan(plan)` tool it must call first
     (chosen over `response_format`, which would terminate the agent before
     it can dispatch; the tool records the plan into state and lets the agent
     keep acting), so the plan is a durable record, not loose internal todos,
  3. dispatches typed scoped implementation sub-agents via `task()`,
  4. may re-emit the plan if it discovers something mid-run (a new
     `requires` edge, an unlisted breaking change).
  The deepagent **plans and delegates only — it never edits code itself.**

- **D4 — Implementation is a fixed set of typed agents, one job each.**
  - **bump-executor (deterministic, no LLM):** applies `apply_bump` for a
    `bump` task. Not a deepagent — it is the deterministic *execution* of a
    `bump` task, invoked by the orchestrator, never an LLM edit.
  - **codemod-adapter (deepagent, sandboxed `FilesystemBackend`,
    `virtual_mode=True`):** given `migration_guide` + scoped `files`, adapts
    call sites, runs `verify` to self-correct, returns `code_diff`.
  - **replacement-migrator (deepagent, sandboxed):** swaps the dependency
    and migrates usage. **Spec A: stubbed** — the task kind and routing
    exist, but `replace` work settles as deferred/skipped (behavior moved
    out of `classify` into the orchestrator). Full implementation is Spec B.

- **D5 — `MigrationPlan` is persisted and reviewable, embedded on the
  remediation record.** It is stored per target on the `Remediation` /
  `RemediationResult` record (not a parallel store) and surfaced through the
  existing artifact mechanism (the same path node-detail panels already
  consume), so a run's plans can be reviewed afterward. `pr_and_persist_node`
  persists the plans alongside the `RemediationResult`.

- **D6 — Uniform planning path (no r1 short-circuit).** Every selected
  target — including a clean r1 bump — goes through the planning deepagent,
  which emits a `MigrationPlan`; `bump` tasks then *execute* deterministically
  via bump-executor (D4). This keeps one code path and uniform plan
  provenance. Skipping the deepagent for the `r1 && !migration_needed` case
  is a deliberately deferred optimization: it is pure, non-breaking, and can
  be added later if planning-call cost matters at corpus scale — not built
  now (YAGNI).

- **D7 — Companion-dep (`requires`) discovery moves into planning.** The
  planner emits `MigrationPlan.requires` from investigation evidence rather
  than relying on ad-hoc mid-run discovery. Mid-run discovery is still
  possible (D3.4, re-emit) but is the exception, not the mechanism. The
  cross-target `connected_groups` coupling in `group_and_verify_gate` is
  unchanged and continues to consume `requires_edges`.

- **D8 — The deterministic backstop is untouched.**
  `group_and_verify_gate`, `replay_and_verify_group`, `apply_group_changes`,
  and `pr_and_persist_node`'s PR/consent flow keep their current behavior.
  `group_and_verify_gate` remains the *only* thing that sets a shipped
  `Remediation.status`; every implementation agent's self-report stays
  provisional.

- **D9 — Honest bounds preserved.** The recursion limit, correction-round
  cap, and group cap in the current design (all failing into
  `skipped`/`failed` *with reasons* rather than crashing) carry over to the
  reshaped orchestrator. A malformed/absent `MigrationPlan` degrades the
  target to `failed` with a reason, exactly as a malformed
  `RemediationOutcome` does today.

## Data model

Two new structured artifacts (in `src/models/remediation.py`):

```python
class ReleaseDigest(BaseModel):        # Release investigator output
    from_version: str | None
    to_version: str | None
    migration_needed: bool             # False => clean bump
    migration_guide: str = ""          # LLM prose, "" when not needed
    breaking_changes: list[str] = []   # itemized

class TargetInvestigation(BaseModel):  # investigate_node output, per target
    target_dep: str
    dependents: list[str]              # Dependency investigator
    call_sites: list[str]              # Source investigator
    release: ReleaseDigest

class MigrationTask(BaseModel):
    kind: Literal["bump", "codemod", "replace"]
    rationale: str
    to_range: str | None = None
    files: list[str] = []              # scope hint for codemod/replace
    replacement_dep: str | None = None
    replacement_range: str | None = None

class MigrationPlan(BaseModel):        # planner output — PERSISTED
    target_dep: str
    tier_hint: Literal["r1", "r2", "r3"]
    migration_guide: str = ""          # carried from ReleaseDigest
    tasks: list[MigrationTask]
    requires: list[str] = []           # companion deps
```

`Remediation` (the shipped record) is largely unchanged: `strategy` is
derived from the task kinds, `migration_plan` (prose) is populated from the
plan's guide, and a reference to the persisted `MigrationPlan` is added for
review. `group_and_verify_gate` still owns the final `status`.

## Architecture

```
classify_targets_node        tier hint (r1/r2/r3) — advisory, no r3 gate  [D1]
        │
investigate_node             per target, fan-out (asyncio.gather):        [D2]
        │                      Dependency (deterministic)
        │                      Source (deterministic)
        │                      Release (version-scoped fetch + 1 LLM digest)
        │                      => TargetInvestigation per target
        │
plan_and_orchestrate         ROOT DEEPAGENT, per target (uniform path):    [D3][D6]
        │                      1. commit_plan -> MigrationPlan (persisted) ─► [D5]
        │                      2. dispatch typed scoped agents via task():
        │                           codemod-adapter (deepagent, sandboxed)
        │                           replacement-migrator (Spec A: stub)      [D4]
        │                         bump tasks executed deterministically
        │
group_and_verify_gate        deterministic clean-clone replay + verify    [D8]
        │   ▲ retry loop (unchanged bounds)                                [D9]
        │
pr_and_persist_node          PR/consent (unchanged) + persist plans        [D5][D8]
```

**Where deepagents is used, and where it is dropped:**
- **Used:** `plan_and_orchestrate` (plan-then-delegate via `task()`), and
  the code-editing agents (`codemod-adapter`, and `replacement-migrator` in
  Spec B) — ReAct-over-tools with sandboxed `FilesystemBackend`.
- **Dropped (now deterministic or a single structured call):** all three
  investigators, and the bump path. The `classify` structured call stays a
  plain `with_structured_output` call as today.

## Retired

- The monolithic `_SYSTEM_PROMPT` do-everything per-target agent in
  `subagent_wrapper.py` (`_run`'s nested `create_deep_agent`). Its
  responsibilities are split across `investigate_node`,
  `plan_and_orchestrate`, and the typed implementation agents.
- `classify_targets_node`'s r3-settle-as-skipped branch (`classify.py`),
  replaced by the tier-as-hint flow (D1) and the orchestrator's `replace`
  stub (D4).

## Success criteria (Spec A)

- The subgraph runs the explicit `classify → investigate → plan_and_orchestrate
  → group_and_verify_gate → pr_and_persist` path; the monolithic per-target
  agent is gone.
- A `MigrationPlan` is produced and persisted for every selected target
  (including a clean r1 bump, whose plan is a single `bump` task), and is
  retrievable for review after a run.
- The Release investigator fetches only changelog entries in the
  `(current, target]` window and produces a `ReleaseDigest` with
  `migration_needed` set correctly for: a clean patch/minor bump
  (`False`), a breaking major with a migration guide (`True`), and a package
  with no resolvable changelog (degrades honestly).
- Every selected target (including a clean r1 bump) produces a persisted
  `MigrationPlan` via the uniform planning path (D6); `bump` tasks execute
  deterministically through bump-executor.
- A `replace` target settles as deferred/skipped through the new orchestrator
  path (no crash), preserving today's r3 behavior until Spec B.
- The deterministic backstop, PR/consent flow, and honest failure bounds
  behave exactly as before (regression-covered by the existing
  `test_remediation_subgraph.py` and unit suites).

## Phasing

- **Spec A (this doc):** the structural spine — investigate → plan → dispatch,
  persisted `MigrationPlan`, bump-executor + codemod-adapter fully
  implemented, replacement-migrator stubbed.
- **Spec B (follow-up):** implement `replacement-migrator` end-to-end (real
  r3 dependency replacement). Depends on A.
- **Companion (already drafted):**
  `2026-08-02-analysis-finding-version-enrichment.md` sharpens the Release
  investigator's target-version resolution; independent ordering.

## Open questions

None blocking; all prior open items resolved:
- Persistence surface — **resolved:** `MigrationPlan` is embedded on the
  `Remediation`/`RemediationResult` record (D5).
- Plan-emission mechanism — **resolved:** a mandatory `commit_plan(plan)`
  tool, not `response_format` (D3.2).
- r1 short-circuit — **resolved:** not built; uniform planning path, the
  short-circuit deferred as a future non-breaking optimization (D6).

## Amendment (2026-08-03): codemod outcome flow via commit_outcome

**Problem found in review (Task 8):** the plan assumed a dispatched subagent's
`RemediationOutcome` would reach the root via `state["remediations"]`. It does
not: deepagents' `task()` returns a subagent's `structured_response` only as
ToolMessage *text* (`deepagents/middleware/subagents.py:610`,
`_EXCLUDED_STATE_KEYS = {"messages","todos","structured_response"}`). Any
*other* channel a subagent returns IS merged into the root state, but the
subagents declared none, so codemod code-diffs were silently lost and replace
targets were mislabeled `bump`.

**Decision (D10):** capture codemod outcomes through a dedicated propagating
channel.
- Add `outcomes: Annotated[dict[str, dict], _merge_replace]` to
  `RemediationDeepAgentState` (and a matching channel on the codemod
  subagent's state schema, `_CodemodState(DeepAgentState)`), so it is NOT
  excluded and merges up.
- Add a `commit_outcome(target_dep, outcome: RemediationOutcome)` tool
  (mirrors `commit_plan`) that returns
  `Command(update={"outcomes": {target_dep: outcome.model_dump()}})`. Give it
  to `codemod_adapter`; its prompt requires calling it as the final step with
  the dependency named in its task description and the unified diff in
  `code_diff`. Drop `response_format` reliance on the codemod agent (it does
  not propagate).
- `_remediations_from_plans` consumes `outcomes` (not the dead
  `agent_remediations`): per target — no plan -> failed; plan has a `replace`
  task -> settled `skipped` "deferred (Spec B)" DETERMINISTICALLY in the node
  (no subagent dispatch); dep in `outcomes` -> build the `Remediation` from
  `RemediationOutcome` fields (`patch=code_diff`, `to_range`, etc.); else a
  planned codemod with no outcome -> failed "codemod produced no outcome";
  else bump-only -> synthesized bump. This also removes the crash risk of
  `Remediation(**outcome_dict)`.
- `replacement_migrator` is no longer dispatched by the planning agent in
  Spec A (replace is deterministic-deferred). `build_replacement_subagent`
  stays defined as the Spec B seam.
