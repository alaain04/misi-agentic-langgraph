# Spec: Analysis Subgraph — Deepagent Swap

**Date:** 2026-07-26
**Scope:** Backend only (`apps/backend`), limited to the internals of
`analysis_subgraph` (`src/main_graph/subgraphs/analysis/`). Frontend
execution-DAG rendering, other subgraphs (discovery, remediation), and
domain-agent tool internals (e.g. a possible future Trivy-based rewrite of
`vulnerability_agent`/`license_agent`) are explicitly out of scope and
untouched.

## Context

A prior spec (`glm-spec.md`, reviewed but not adopted as-is) proposed
replacing the analysis subgraph's orchestration with
`deepagents.create_deep_agent` + a QuickJS code-interpreter, framed as
applying Recursive Language Models (RLM). Review against the actual codebase
found the stated problem ("one unpredictable ReAct agent") did not match
current architecture — analysis is already a two-level, LangGraph-native
fan-out (`analysis_conductor` → `Send()` → `domain_agent`, bounded ReAct
loops with a self-critique gate) — and the proposed fix (a nested,
opaque-to-LangGraph agent runtime with broad `execute_command` tool access)
would have regressed observability, typed-output discipline, and
deterministic-enforcement properties this codebase relies on and has been
burned by abandoning before (see `docs/superpowers/specs/2026-07-20-direct-anchored-findings.md`
D7, and the maintainer-count prompt-only-rule incident it references).

Follow-up discussion identified a materially different, smaller-footprint
option: swap only what's *inside* `build_analysis_subgraph()`, preserving its
exact input/output contract with the rest of the main graph, and reuse
today's domain-agent logic unchanged via `deepagents`' `CompiledSubAgent`
mechanism (a documented capability: any LangGraph `CompiledStateGraph` can be
passed as a subagent, invoked through the framework's own `task()` tool,
instead of running its own built-in ReAct loop). This spec covers that
narrower swap.

**Why do this at all:** the user wants to evaluate `deepagents` as a
technology on a real, contained, reversible slice of the codebase before
considering it anywhere else. The genuine capability delta it offers over
today's hand-rolled fan-out is context offload for a single agent (virtual
filesystem + persistent plan, so one investigation can reason over more
material than fits in one context window) and native arbitrary-depth
subagent recursion — not raw fan-out breadth, which `Send()` already
provides. This is explicitly a framework-evaluation goal, not a fix required
by the reliability roadmap (`docs/superpowers/roadmap.md` workstreams A–D
don't depend on it).

## Problem being preserved, not just the framework swap

The conductor's dispatch sharding for package-scoped custom-concern agents
(`web_research_agent`, `maintenance_agent`, `supply_chain_agent`) is an LLM
judgment call, capped at ≤5 parallel dispatches/iteration × 4 iterations
(`analysis_conductor.py:17,42`). For a repo with many direct dependencies,
nothing guarantees every one gets evaluated. The swap must not lose ground on
this — it must guarantee coverage at least as well as a deterministic
alternative would, not merely trade one non-deterministic sharding strategy
for another.

## Decisions

- **D1 — Swap boundary is the subgraph, not individual nodes.** Replace
  `analysis_conductor`, `_after_conductor`, `domain_agent`, and
  `evidence_collector` (`graph.py:57-73`). Keep the subgraph's contract with
  `main_graph` identical: in `{job_id, concern, prep_result_id}`, out
  `{analysis_result_id}`. `save_analysis_result` (dedup, severity filter,
  `AnalysisResult` assembly) is **unchanged** — it only needs `bundle_ids` in
  state, not how they were produced.
- **D2 — Domain-agent logic is reused unchanged via `CompiledSubAgent`.** One
  `CompiledSubAgent` per current `agent_type`
  (`vulnerability_agent`, `license_agent`, `supply_chain_agent`,
  `maintenance_agent`, `web_research_agent`). Each wrapper is a thin one-node
  `CompiledStateGraph` that calls `agent_class().run(dispatch, prep,
  container, cache)` exactly as `domain_agent.py:17` does today —
  `_react_loop`, `critique_findings`, and the deterministic `LicenseAgent`
  path are untouched.
- **D3 — No code-execution tool surface.** The root deep agent's tools are
  `task()` dispatch to the five `CompiledSubAgent`s plus deepagents' built-in
  virtual filesystem/todo tools. No `CodeInterpreterMiddleware`, no
  `execute_command`. This is a deliberate deviation from `glm-spec.md`: the
  context-offload benefit comes from the virtual filesystem alone, and
  dropping code execution keeps the tool surface close to today's
  fixed-signature, container-scoped registry rather than opening an
  LLM-authored-command-execution risk.
- **D4 — Results leave the deep agent run via a custom `state_schema`, the
  same reducer pattern `AnalysisState` already uses.** Verified directly
  against installed `deepagents==0.6.12` source
  (`deepagents/middleware/subagents.py`, `_build_task_tool`/
  `_return_command_with_state_update`): when a `CompiledSubAgent`'s
  `runnable` returns state, every key other than `messages`/`todos`/
  `structured_response` is merged into the **root** deep agent's state via
  `Command(update=state_update, ...)` — i.e. subagent state updates flow back
  to the root through ordinary LangGraph reducers, not just as a summarized
  `ToolMessage`. So: define `AnalysisDeepAgentState(DeepAgentState)` adding
  `bundle_ids: Annotated[list[str], operator.add]` and
  `agent_calls: Annotated[list[dict], operator.add]`, pass it as
  `create_deep_agent(..., state_schema=AnalysisDeepAgentState)`. Each
  `CompiledSubAgent` wrapper node saves its `EvidenceBundle` via
  `dao.save_bundle` (unchanged) and returns
  `{"messages": [...], "bundle_ids": [bundle_id], "agent_calls": [record.model_dump()]}`
  — no config-threaded side channel needed. (`prep_result_id`/`job_id` flow
  the same direction in reverse: `_validate_and_prepare_state` passes the
  root's own state through to each subagent minus only
  `messages`/`todos`/`structured_response`/agent-private keys, so seeding
  `create_deep_agent`'s initial state with `prep_result_id`/`job_id` makes
  them available inside every subagent's state automatically, matching how
  `AnalysisState` keys already flow into subgraphs by name today.) After
  `ainvoke()` returns, the wrapping node reads `bundle_ids`/`agent_calls`
  straight off the final state.
- **D5 — Coverage guarantee is deterministic, layered on top of the agent,
  not trusted to it.** The root agent is free to call `task()` however it
  judges best (mirrors today's conductor flexibility). Before the subgraph
  may proceed to `save_analysis_result`, a deterministic check compares the
  union of `packages_to_focus` across accumulated `AgentCallRecord`s for
  package-scoped agent types (`web_research_agent`, `maintenance_agent`,
  `supply_chain_agent` — `vulnerability_agent`/`license_agent` stay exempt,
  same set as today's `_WHOLE_TREE_AGENTS`) against the full direct-dep list
  from `prep.dependency_graph`. Gaps trigger one bounded corrective
  re-invocation (feed the agent the uncovered list, let it resume, capped at
  2 rounds). Deps still missing after that are dispatched directly by a
  deterministic backstop — a plain `agent_class().run()` call per missing
  dep, no further LLM involvement — using whichever package-scoped agent
  type(s) actually ran at least once this job (preserves multi-concern
  coverage without inventing a new default).
- **D6 — Bounded by construction, not LLM discipline.** A `CompiledSubAgent`
  is a flat one-node graph — it cannot itself spawn further subagents, so
  there is no unbounded recursion risk regardless of the root agent's
  decisions. `deep_agent.ainvoke(state, config)` additionally sets
  `recursion_limit` in its `RunnableConfig` (a native LangGraph guarantee) as
  a hard backstop, the same role `_MAX_ITERATIONS` plays today.
- **D7 — Failure handling matches current behavior, not more, not less.**
  Top-level exceptions from the deep agent run are not caught — same
  fail-fast contract `domain_agent.py` has today (an exception fails the
  node, the job goes to `failed`). Per-dependency backstop dispatch failures
  *are* caught individually and logged (`logger.warning`, same pattern as
  `npm_audit`), since a single backstop failure should not void otherwise-good
  coverage.
- **D8 — Whole-tree dispatch capping moves into the wrapper, not the
  conductor.** `drop_repeat_whole_tree_dispatches` (`analysis_conductor.py:94`)
  currently caps `vulnerability_agent`/`license_agent` to one run per job by
  filtering the conductor's dispatch list — that conductor is removed by D1,
  so this needs a new home. Each of those two `CompiledSubAgent` wrappers
  checks `state["agent_calls"]` (D4's passthrough state, visible inside the
  subagent the same way it accumulates on the root) before doing real work: if
  an `AgentCallRecord` for its own `agent_type` is already present, it returns
  the existing `bundle_id` as a no-op instead of re-running `agent_class().run()`.
  This is a straight port of the existing rule to the new integration point,
  not a new behavior — enforced deterministically in code, matching D7 of the
  direct-anchored-findings spec's "deterministic enforcement over prompt-only"
  precedent.

## Data flow

```
START
  -> analysis_deepagent_node
       - builds initial deep-agent input: concern, prep context, direct-dep
         list, agent roster (mirrors today's analysis_conductor prompt),
         plus job_id/prep_result_id (flow into every subagent's state
         automatically per D4)
       - calls deep_agent.ainvoke(input, config) — deep agent plans, calls
         task() against the 5 CompiledSubAgents zero or more times per its
         own judgment; each call's state update (bundle_ids/agent_calls)
         merges into the root's state via reducers (D4)
       - reads bundle_ids / agent_calls off the final root state
  -> coverage_gate
       - computes missing = direct_deps - covered (package-scoped agents only)
       - if missing and correction_rounds < 2: re-invoke deep agent with the
         gap list, loop back to analysis_deepagent_node
       - if missing and rounds exhausted: deterministic backstop dispatch
         per missing dep, no LLM
  -> save_analysis_result   (unchanged)
  -> END
```

## Out of scope

- Frontend execution-DAG rendering for this subgraph (`ConductorArtifact`/
  `ToolRunnerArtifact`/`AgentCallRecord`-driven UI will go stale for this
  subgraph specifically until a follow-up bridges deepagents' own event
  stream to the artifact schema). Explicitly deferred per user direction.
- Any change to `discovery` or `remediation` subgraphs. The reliability
  roadmap's argument against agentic tool freedom in remediation
  (`docs/superpowers/roadmap.md` workstream A/C) still holds regardless of
  this spec's outcome.
- Any change to domain-agent tool internals (`npm_audit`, license data
  tables, etc.), including a possible future Trivy-based consolidation —
  kept as a loosely-coupled future swap by design (D2).
- Reproducing today's fine-grained per-iteration observability inside the
  deep agent's own planning loop.

## Verified against the real library (resolves the prior open risk)

Confirmed by installing `deepagents==0.6.12` into a scratch venv and
introspecting it directly (not from docs alone):

- `CompiledSubAgent` is `{name: str, description: str, runnable: Runnable}`.
  The `runnable` must be a `Runnable`/`CompiledStateGraph` whose state
  includes a `messages` key; it does **not** inherit
  `create_deep_agent`'s `state_schema`, so if it needs custom fields it must
  declare its own compatible schema.
- `create_deep_agent(model, tools=None, *, system_prompt=None,
  subagents=None, state_schema=None, checkpointer=None, ...)` — `subagents`
  accepts a mix of `SubAgent` | `CompiledSubAgent` | `AsyncSubAgent`.
- The `task` tool (`deepagents/middleware/subagents.py::_build_task_tool`)
  seeds each subagent invocation from a filtered copy of the root's own state
  (`_EXCLUDED_STATE_KEYS = {"messages", "todos", "structured_response"}` are
  stripped; everything else, including custom fields like `job_id`/
  `prep_result_id`, passes through) plus a fresh `messages: [HumanMessage(description)]`.
  It returns `Command(update={**state_update, "messages": [ToolMessage(...)]})`,
  where `state_update` is every key the subagent returned other than the
  three excluded ones — i.e. custom state **does** flow back to the root
  through ordinary reducers, confirming D4 as written above without a
  side-channel.
- Pin `deepagents>=0.6.12,<0.7` in `pyproject.toml` (the introspected
  version); re-verify this section if the plan is executed against a newer
  major/minor.

One mechanical detail this exposed, left to the plan rather than re-opening
this spec: the root agent communicates a task to a subagent as free text (the
`description` argument to `task()`), not a structured `AgentDispatch`. Each
`CompiledSubAgent` wrapper's entry step must convert that text into
`AgentDispatch(domain, hypothesis, packages_to_focus)` — via one small
`with_structured_output` call, matching this codebase's existing structured-output
discipline, not regex/text parsing — before calling `agent_class().run()`.

## Success criteria

- `analysis_subgraph`'s external contract is byte-identical:
  `evidence_correlator`/`finding_reviewer`/`report_builder` require no
  changes.
- Every direct dependency has at least one `AgentCallRecord` from a
  package-scoped agent type by the time `save_analysis_result` runs, for any
  concern that dispatches at least one package-scoped agent — enforced
  deterministically (D5), verified by an integration test that stubs the deep
  agent to leave gaps and asserts the backstop closes them without an LLM
  call.
- `vulnerability_agent`/`license_agent` (whole-tree agents) continue to run
  at most once per job, via the wrapper-level no-op check in D8.
- No `execute_command`-class tool is reachable from the deep agent or any
  subagent.
- Full backend suite, ruff, and mypy green; new coverage-gate logic has unit
  tests with no LLM dependency, matching the style of
  `drop_repeat_whole_tree_dispatches`/`dedup_findings` tests today.
