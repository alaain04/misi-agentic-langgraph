# Spec: Remediation Flatten — Deterministic Batched Planning, Single Flat Execution Agent per Group

**Date:** 2026-08-08
**Scope:** Backend only (`apps/backend`), remediation subgraph
(`src/main_graph/subgraphs/remediation/`). Supersedes D3/D4/D6 of
`2026-08-02-remediation-planner-decomposition-design.md`: retires the
planner-agent that plans-and-dispatches, replacing it with a deterministic
batched planning step and a single flat execution agent invoked per coupled
group, with no agent dispatching another agent.
**Status:** Spec. D1/D2 (classify, investigate) and D8/D9 (deterministic
backstop, honest bounds) of the prior spec are unaffected and retained as-is.

## Context

The 2026-08-02 decomposition replaced the old monolithic per-target agent
with `classify → investigate_node → plan_and_orchestrate → group_and_verify_gate
→ pr_and_persist_node`. As built (`deepagent/nodes.py::root_deepagent_node`),
`plan_and_orchestrate` is itself a `create_deep_agent` whose only real job is:
emit a `MigrationPlan` per target via a mandatory `commit_plan` tool, then
dispatch `codemod_adapter` — a **second, nested** `create_deep_agent`
(`subagent_wrapper.py::build_codemod_subagent`) — via `task()`. This is an
agent dispatching an agent, and the outer agent's job (read investigation
evidence already gathered by `investigate_node`, decide task kind per target)
does not need multi-turn tool-calling at all — it's a single decision made
from data that's already fully assembled by the time the agent starts.

Consequences observed in practice:

- **The rate-limit bugfix (2026-08-08) was a workaround for this shape, not a
  necessary feature.** deepagents' own baked-in prompt tells the coordinator
  to fan out `task()` concurrently; `ToolNode` executes that fan-out with no
  cap. A `TARGET_SEMAPHORE` + shared rate limiter were added purely to survive
  the outer agent's own dispatch pattern — a problem that doesn't exist if
  nothing is dispatching agents via `task()` in the first place.
- **The `commit_plan` tool + `Command`/`ToolMessage` plumbing already caused
  one production crash** (missing terminating `ToolMessage`, fixed
  2026-08-08). Every custom `Command`-returning tool is a new surface for
  this class of bug; a plain node return has none.
- **A "clean" bump is applied but never verified until the deterministic
  gate.** Today, when a `MigrationPlan` has no `codemod`/`replace` task,
  `_remediations_from_plans`'s bump branch never calls `apply_bump` or
  `verify` — it just carries `to_range` through from the plan. `apply_bump`
  only actually runs later, declaratively, inside `replay_and_verify_group`
  (clean-clone) and `apply_group_changes` (PR). If the release digest missed
  something and a "safe" bump actually breaks the build, that's caught for
  the first time at `group_and_verify_gate`, and the only recovery path is
  routing the whole target back through `root_deepagent_node`, which re-runs
  the **entire planning agent from scratch** with no memory of what the first
  attempt tried — planning is redone, not just execution.
- **Retry has no failure evidence.** `route_after_group_verify`'s retry edge
  sends `retry_targets` back to re-plan-and-dispatch; nothing carries
  `VerificationResult.logs_snippet` into that retry. The 2026-08-02 spec
  explicitly named this a non-goal ("Failure-log-informed repair loop").

The deterministic backstop — `group_and_verify_gate` / `replay_and_verify_group`
(fresh-clone replay, never trusting agent self-report) and
`pr_and_persist_node` — remains the mature, correct part of the subgraph and
is **retained unchanged**. This spec reshapes only the middle: planning and
execution.

## Goals

1. Replace `plan_and_orchestrate` with a deterministic node
   (`build_migration_plan_node`) that emits every target's `MigrationPlan` via
   **one batched structured-output call**, not a multi-turn agent loop —
   dropping the agent layer wherever the decision is single-shot.
2. Collapse execution to **one flat `create_deep_agent` invoked directly per
   connected group** (`connected_groups`, same algorithm already used at the
   gate) — never dispatched through another agent's `task()` tool. Python's
   own bounded `asyncio.gather` provides cross-group parallelism instead of
   relying on (and fighting) deepagents' internal fan-out.
3. **Fold `bump` targets into the same execution agent as `codemod`
   targets.** A plan's `kind` becomes a hint about expected effort, not a
   hard routing split: the agent calls `bump_dependency` + `verify` for a
   `bump` plan and only escalates to search/edit if `verify` fails or the
   plan already says `codemod`. This extends the two-tier verify philosophy
   already documented on the `verify` tool ("a guide for your own next
   step... a separate deterministic check re-verifies from a clean clone")
   to cover bump instead of skipping it.
4. On a `group_and_verify_gate` failure, **repair by re-invoking the same
   group's execution agent** with the verification failure log and its own
   prior trace — not by re-running planning.
5. Keep `replace` a deterministic stub (Spec B still not built) — relocate
   its handling out of the retired orchestrator into a plain step, behavior
   unchanged.

## Non-goals

- Real dependency replacement (Spec B).
- HITL plan-review gate (still at `pr_and_persist_node`).
- Any change to `classify_targets_node` or `investigate_node`'s investigator
  logic (D1/D2 of the prior spec stand as-is).
- Locus-level task granularity.

## Decisions

- **D1 — Planning becomes one deterministic, batched node
  (`build_migration_plan_node`), replacing `plan_and_orchestrate`'s planning
  half.** One `with_structured_output` call is given every target's
  `TargetInvestigation` + tier hint at once and returns
  `dict[target_dep, MigrationPlan]` directly as a node return — no tool call,
  no agent loop, no recursion limit (nothing here can run away). Seeing all
  targets together preserves the prior spec's cross-target `requires`
  reasoning (D7) without needing a mid-run re-emit mechanism — dropped as
  YAGNI; there's no evidence it was ever exercised. `MigrationPlan` and
  `MigrationTask` are unchanged models; `kind` is now read by the execution
  agent as a hint, not a routing key (D3 below). A malformed/absent plan for
  a target still degrades honestly to `failed`, per D9 of the prior spec.

  **Retry-discovered companion targets:** a dep named only via some other
  target's `requires` (never in the original selection) has no plan from the
  batch call. `remediate_targets_node` (D2) synthesizes a bare
  `RemediationTarget` for it exactly as `_resolve_working_targets` does
  today, then calls the *same* planning function scoped to that one target
  before dispatching it — a single-target call, not a re-run of the batch.

- **D2 — Execution collapses to ONE flat agent per group, replacing
  `root_deepagent_node` with `remediate_targets_node`.** No nested `task()`
  dispatch anywhere in this path.
  1. Split off `replace`-kind targets: settled deterministically as
     `skipped` / "deferred (Spec B)", same logic `_remediations_from_plans`
     already has, just relocated since there's no orchestrator left to house
     it.
  2. `connected_groups(bump_and_codemod_targets, requires_edges)` — the same
     grouping algorithm `group_and_verify_gate` already uses, now applied one
     step earlier, before execution instead of only at verification.
  3. For each group, invoke `build_codemod_subagent`'s compiled graph
     **directly** (not via `task()`), given every member's plan
     (`kind`, `migration_guide`, `call_sites`) in one initial message. Prompt:
     for a `bump`-kind member, call `bump_dependency` then `verify` and stop
     unless `verify` fails; otherwise (or on that escalation) search/edit
     until `verify` is green or no safe fix exists. `commit_outcome` per
     target either way — this tool and `_CodemodState`'s `outcomes` channel
     are unchanged.
  4. Groups run concurrently via `asyncio.gather`, bounded by
     `TARGET_SEMAPHORE` — repurposed from "cap concurrent nested per-target
     agents" to "cap concurrent per-group agents." Same mechanism, no new
     concurrency primitive.
  5. Assemble `remediations`/`requires_edges` from the combined `outcomes` +
     `migration_plans` + the replace stubs from step 1 — the same shape
     `_remediations_from_plans` produces today, simplified: the bare-bump
     special case (no dispatch, no verify) goes away since every non-replace
     target now produces a real `RemediationOutcome`.

- **D3 — Repair re-invokes the failing group's own execution agent with
  failure evidence, instead of restarting planning.** Today's retry edge
  (`route_after_group_verify` → back to the planner node) already routes
  `retry_targets` to the same node; the change is in what that node does with
  a retry. `remediate_targets_node`, on a retry round, (a) reuses each
  retry target's existing `MigrationPlan` from `state["migration_plans"]`
  instead of re-planning, (b) re-invokes that group's execution agent with
  the prior `VerificationResult.logs_snippet` and the agent's own message
  history from the failed attempt appended to the task description, so the
  agent is diagnosing a concrete failure, not starting cold. Still bounded by
  the existing `_MAX_CORRECTION_ROUNDS`; exhausting it settles `failed` with
  a reason exactly as today. This closes the "failure-log-informed repair
  loop" item the prior spec explicitly deferred.

- **D4 — The deterministic backstop is untouched.** `group_and_verify_gate`,
  `replay_and_verify_group`, `apply_group_changes`, `pr_and_persist_node`'s
  PR/consent flow, and the recursion/correction-round/group caps all carry
  over unchanged. `group_and_verify_gate` remains the *only* thing that sets
  a shipped `Remediation.status`; the execution agent's `commit_outcome`
  stays provisional, same as `RemediationOutcome`'s docstring already states.

## Data model

No changes to `MigrationPlan`, `MigrationTask`, `TargetInvestigation`,
`ReleaseDigest`, or `RemediationOutcome` (`src/models/remediation.py`) — all
reused as-is.

`RemediationDeepAgentState` (`deepagent/state.py`) is **retired**: it existed
to give the planner-agent's `task()` dispatch a shared `migration_plans`/
`outcomes` channel across nested calls. Replaced by:
- `migration_plans` written directly as `build_migration_plan_node`'s plain
  return value (no `commit_plan` tool, no `Command`, no `ToolMessage`).
- `outcomes` stays exactly as `_CodemodState`'s channel (`_merge_replace`),
  now populated by one agent instance per group instead of one per target.

## Architecture

```
classify_targets_node         tier hint (r1/r2/r3), advisory -- UNCHANGED
        |
investigate_node              per target, deterministic + 1 LLM digest -- UNCHANGED
        |
build_migration_plan_node     ONE batched structured-output call over ALL       [D1]
        |                       targets -> dict[target_dep, MigrationPlan]
        |
remediate_targets_node        replaces root_deepagent_node:                     [D2][D3]
        |                       1. split off `replace` -> deterministic skip
        |                       2. connected_groups() over bump+codemod targets
        |                       3. per group: ONE flat execution agent, direct
        |                          invocation (no task() dispatch)
        |                       4. bounded concurrency across groups
        |                          (TARGET_SEMAPHORE)
        |                       5. assemble remediations/requires_edges
        |
group_and_verify_gate         clean-clone replay + verify -- UNCHANGED          [D4]
        |    \
        |     +-- fail, budget left --> retry_targets loops back to            [D3]
        |                                remediate_targets_node, which reuses
        |                                the existing plan and re-invokes only
        |                                the failing groups' agents WITH the
        |                                verification failure log attached
        v
pr_and_persist_node            PR/consent + persist plans -- UNCHANGED
```

**Where deepagents is used, and where it is dropped (updated from the prior
spec):**
- **Used:** exactly one place now — the per-group execution agent
  (`build_codemod_subagent`, unchanged tool set), invoked directly, never via
  `task()`.
- **Dropped:** the outer planner/orchestrator agent entirely. Planning is a
  batched structured call; bump execution is no longer a silent no-op
  data-carry but runs inside the same execution agent as codemod, just
  usually exiting after one `bump_dependency` + `verify` pair.

## Retired

- `root_deepagent_node` / `plan_and_orchestrate`, `_build_planning_agent`,
  `_PLANNER_PROMPT`, and its `GraphRecursionError` handling (nothing left at
  the planning layer can recurse; the execution agent keeps its own bounded
  loop via `_RECURSION_LIMIT` on its own graph, unaffected).
- `make_commit_plan_tool` (`deepagent/tools.py`) — nothing calls it once
  planning is a bare structured-output call.
- `RemediationDeepAgentState` (`deepagent/state.py`).
- The bare-bump special case in `_remediations_from_plans`'s final `else`
  branch — every non-replace target now produces a real `RemediationOutcome`
  via `commit_outcome`, so the branch collapses to the same shape as the
  existing codemod/outcome branches.

## Success criteria

- The subgraph runs `classify → investigate → build_migration_plan_node →
  remediate_targets_node → group_and_verify_gate → pr_and_persist_node`; no
  node in this path dispatches another agent via `task()`.
- A `MigrationPlan` is still produced and persisted for every selected
  target, including retry-discovered companion deps, via one batched call
  per planning pass (not one call per target).
- A `bump`-kind target that turns out to break the build (undetected by the
  release digest) is caught by the execution agent's own `verify` call, not
  only by `group_and_verify_gate` — and self-corrects via the same
  search/edit loop `codemod` targets already use.
- A group that fails `group_and_verify_gate` is repaired by re-invoking its
  own execution agent with the failure log and its prior trace, without
  redoing planning, bounded by the existing correction-round cap.
- `TARGET_SEMAPHORE` bounds concurrent *group* agents; no unbounded
  LLM fan-out is reachable from this subgraph (the rate-limit bugfix's root
  cause — deepagents' own `task()` fan-out prompt — no longer applies, since
  nothing in this path dispatches via `task()`).
- The deterministic backstop, PR/consent flow, and honest failure bounds
  behave exactly as before (regression-covered by the existing subgraph and
  unit test suites).

## Open questions

- **Batch size for `build_migration_plan_node`.** A very large finding set
  could push all targets' `TargetInvestigation` payloads past a comfortable
  context budget in one call. Not expected to matter at current corpus
  scale; if it does, chunk the batch call rather than reintroducing a
  per-target agent turn for planning.
- **Trade-off accepted, not open:** every target now costs at least one
  execution-agent LLM turn (even a trivial patch bump, which today costs
  zero LLM calls at execution time — `apply_bump` runs declaratively off the
  plan with no `verify` in the loop). This was a deliberate choice: catching
  release-digest blind spots and enabling trace-informed repair (D3) is
  judged worth the added per-target cost.
