# Structured concern + router for the analysis subgraph

## Problem

The analysis subgraph always routes every concern through the full deep
agent (`analysis_deepagent_node` -> `coverage_gate` -> possible
`backstop_dispatch`), regardless of how simple the concern is. A concern like
"check for known vulnerabilities" needs nothing beyond a single
`vulnerability_agent` whole-tree scan, yet it still pays for:

- the deep agent's own LLM planning loop (`create_deep_agent`, GPT-5.4-mini),
- `subagent_wrapper._extract_dispatch`'s free-text-to-`AgentDispatch` LLM call,
- `coverage_gate`'s `whole_tree_scan_satisfies_concern` LLM judge call
  (`deepagent/coverage.py`, added in
  `2026-08-01-concern-aware-coverage-gate-design.md`) to confirm what was
  already knowable from the concern alone.

`concern` also travels through the whole pipeline as an opaque string
(`MainState.concern: str` -> `AnalysisState.concern: str`), so every LLM
judge that needs to reason about it (the coverage gate, the deep agent's own
planning) re-derives the same classification from free text instead of
looking it up.

## Goal

Split the analysis subgraph into two branches after a single
concern-understanding step:

```
understand_concern -> run_direct_agents -> route_concern
                                              |-- simple  --> save_analysis_result
                                              `-- complex --> analysis_deepagent_node --> coverage_gate --> ...
```

(Superseded by the 2026-08-02 amendment below: `run_direct_agents` now runs
unconditionally as a whole-tree prefix, and `route_concern` is evaluated
*after* it rather than branching directly out of `understand_concern`.)

- `understand_concern`: one LLM structured-output call that turns the raw
  concern string into a typed `Concern` and writes it to state. This is the
  only place concern classification happens — nothing downstream re-derives
  it from free text.
- `route_concern`: a plain Python conditional-edge function. Reads
  `state["structured_concern"]` (already written by `understand_concern`)
  and returns `"simple"` or `"complex"`. It never computes or mutates state
  itself.
- Simple concerns (vulnerability and/or license, whole-tree, no per-dependency
  breakdown required) run directly against the relevant whole-tree agent(s),
  bypassing the deep agent, `_extract_dispatch`, and `coverage_gate`
  entirely.
- Complex concerns go through the existing deep agent pipeline, unchanged in
  graph wiring but with a rewritten, more opinionated system prompt, an
  enforced call budget (`DeepAgentLimits`), and `coverage_gate`'s
  per-direct-dependency enforcement now conditioned on
  `structured_concern.requires_per_dependency_analysis` (section 8) instead
  of applying unconditionally.

## Non-goals

- Not changing `MainState.concern`'s type or the `/analyze` API contract — it
  stays a free-text string. `Concern` is purely internal to the analysis
  subgraph.
- Not changing `whole_tree_scan_satisfies_concern`'s signature — it still
  takes the raw concern string, and only runs (via `coverage_gate`) when the
  deep agent path is taken AND `requires_per_dependency_analysis=True` (see
  section 8). `coverage_gate` itself does change: it now reads one field off
  `structured_concern` to decide whether to enforce coverage at all.
- Not changing `backstop.py`, `license_agent.py`, `vulnerability_agent.py`,
  or any other `BaseAgent` subclass.
- Not fixing `docs/graphs.md` / `docs/backend/architecture.md` — both already
  describe a pre-deepagent architecture unrelated to this change; left alone
  per explicit decision.

## Architecture / data flow

### 1. `Concern` schema

New file `apps/backend/src/main_graph/subgraphs/analysis/concern.py`:

```python
ConcernType = Literal[
    "vulnerability", "license", "maintenance", "supply_chain",
    "web_research", "other",
]
ConcernScope = Literal["all_dependencies", "specific_packages"]

class Concern(BaseModel):
    type: list[ConcernType]
    scope: ConcernScope
    packages: list[str] = Field(default_factory=list)  # set iff scope == "specific_packages"
    requires_per_dependency_analysis: bool
    preferred_agents: list[str]

SIMPLE_CONCERN_TYPES = {"vulnerability", "license"}

def is_simple(concern: Concern) -> bool:
    return (
        set(concern.type) <= SIMPLE_CONCERN_TYPES
        and not concern.requires_per_dependency_analysis
        and concern.scope == "all_dependencies"
    )
```

`type` is a list (not a single scalar) so a concern spanning both
vulnerability and license reads as `["vulnerability", "license"]` rather than
needing a synthetic `"combined"` value.

`is_simple` is the only place the simple/complex boundary is decided, and it
never touches an LLM — the classification already happened in
`understand_concern`.

### 2. `understand_concern` node

New file
`apps/backend/src/main_graph/subgraphs/analysis/nodes/understand_concern.py`,
wired as the **first** node in the subgraph:

```python
async def understand_concern(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    direct_deps = list(prep.dependency_graph.get("direct", {}).keys())

    structured = _llm.with_structured_output(Concern, method="function_calling")
    concern = await structured.ainvoke([
        {"role": "system", "content": _UNDERSTAND_CONCERN_SYSTEM.format(
            direct_deps=direct_deps, agent_roster=_roster(),
        )},
        {"role": "user", "content": state["concern"]},
    ])
    return {"structured_concern": concern.model_dump()}
```

Stored as a `dict` (`Concern.model_dump()`), matching how `agent_calls` and
`deepagent_state` are already stored in `AnalysisState` — not a live pydantic
instance — for consistency with the rest of the state shape and safe
checkpointing.

`AnalysisState` (`state.py`) gains:

```python
structured_concern: NotRequired[dict]  # Concern.model_dump()
```

`NotRequired` because, like every other internal field here, it doesn't
exist in the state LangGraph hands off from `MainState` — it's `NotRequired`
right up until `understand_concern` runs, which is always the first thing
that happens in this subgraph.

### 3. `route_concern` conditional edge

```python
def route_concern(state: AnalysisState) -> str:
    concern = Concern(**state["structured_concern"])
    return "simple" if is_simple(concern) else "complex"
```

Wired via `builder.add_conditional_edges("understand_concern", route_concern, {"simple": "run_direct_agents", "complex": "analysis_deepagent_node"})`.

### 4. Simple path — `run_direct_agents`

New file
`apps/backend/src/main_graph/subgraphs/analysis/nodes/run_direct_agents.py`.
For each `agent_type` in `concern.preferred_agents`:

```python
dispatch = AgentDispatch(
    domain=", ".join(concern.type),
    hypothesis=state["concern"],   # raw text, same value the deep agent path shows the user
    packages_to_focus=[],          # ignored by whole-tree agents anyway
    agent_type=agent_type,
)
```

then calls the new shared helper `run_specialist(agent_type, dispatch, prep,
svc)` (see below), gathering all `preferred_agents` concurrently via
`asyncio.gather`, bounded by the same global semaphore described in section
6. Returns `{"bundle_ids": [...], "agent_calls": [...]}` — the same shape
`analysis_deepagent_node` already returns, so `save_analysis_result`
(`nodes/save_analysis_result.py`) needs no changes; it only ever reads
`bundle_ids`/`agent_calls`/`concern`/`job_id` off `AnalysisState`.

Per the earlier decision, the simple branch wires straight to
`save_analysis_result`, skipping `coverage_gate`/`backstop_dispatch` — both
exist solely to guarantee per-package coverage, which is impossible to be
missing here (whole-tree agents ignore `packages_to_focus` by construction;
see `license_agent.py`'s docstring).

### 5. Shared `run_specialist` helper

New file
`apps/backend/src/main_graph/subgraphs/analysis/deepagent/specialist_runner.py`,
extracted from the body of `subagent_wrapper._run` (currently inlined,
`subagent_wrapper.py:106-129`):

```python
async def run_specialist(
    agent_type: str, dispatch: AgentDispatch, prep: PrepResult, svc: dict,
) -> tuple[str, dict]:
    """Runs one specialist agent, saves its bundle, returns (bundle_id, AgentCallRecord dict)."""
    agent_class = REGISTRY[agent_type]
    started_at = datetime.now(UTC).isoformat()
    bundle, tools_used, react_iterations = await agent_class().run(
        dispatch, prep, svc["container"], cache=svc.get("input_cache")
    )
    finished_at = datetime.now(UTC).isoformat()
    bundle_id = await svc["result_dao"].save_bundle(bundle)
    record = AgentCallRecord(
        conductor_iteration=0, agent_type=agent_type, domain=dispatch.domain,
        packages_to_focus=dispatch.packages_to_focus, tools_used=tools_used,
        react_iterations=react_iterations, started_at=started_at,
        finished_at=finished_at, bundle_id=bundle_id,
    )
    return bundle_id, record.model_dump()
```

Both `subagent_wrapper._run` (after its `_extract_dispatch` call) and
`run_direct_agents` call this instead of duplicating the "run agent -> save
bundle -> build `AgentCallRecord`" sequence.

### 6. `DeepAgentLimits`

New file `apps/backend/src/main_graph/subgraphs/analysis/deepagent/limits.py`:

```python
@dataclass(frozen=True)
class DeepAgentLimits:
    max_specialist_calls: int = 8
    max_parallel_calls: int = 3

DEEPAGENT_LIMITS = DeepAgentLimits()
```

Enforced in `subagent_wrapper.py`:

- **Concurrency** — a module-level `asyncio.Semaphore(DEEPAGENT_LIMITS.max_parallel_calls)`,
  shared process-wide (the deep agent, and therefore every subagent
  runnable, is already a module-level singleton reused across jobs — see
  `nodes.py:90`, `_deep_agent = _build_deep_agent()` — so the semaphore
  bounds concurrent specialist work across the whole process, matching the
  stated goal of not overwhelming shared downstream APIs regardless of how
  many jobs are running). Acquired around dispatch-extraction +
  `run_specialist`.
- **Budget** — before doing any work, `_run` checks
  `len(state.get("agent_calls") or [])` (already the root deep agent's
  accumulated call count for this job, via state passthrough) against
  `max_specialist_calls`. At or over budget, skip the specialist entirely and
  return a message telling the root LLM the budget is exhausted instead of
  running it, so the agent finalizes with whatever it already has (per the
  "prioritize highest-risk, report unanalyzed" prompt instruction below).
  **Open question for implementation**: `_run`'s current success path always
  returns `messages: []`; whether a non-empty message here actually becomes
  visible as the `task()` tool's result text depends on how
  `deepagents==0.6.12`'s `CompiledSubAgent` wraps a subagent runnable's
  final state into a tool response. Verify against the installed
  `deepagents` source before relying on it; if `messages` isn't surfaced,
  the fallback is returning an error `ToolMessage`-equivalent structure
  `deepagents` does expose for tool failures.
- `run_direct_agents` does not need budget enforcement — `preferred_agents`
  for a simple concern is at most `{vulnerability_agent, license_agent}`, so
  it can never approach either limit.

### 7. Deep agent prompt rewrite

`_SYSTEM_TEMPLATE` (`deepagent/nodes.py`) is restructured around "produce the
smallest complete plan" instead of "collect evidence until satisfied":

```
You are a dependency risk investigation agent for a Node.js project. You are
invoked only for concerns a deterministic router already classified as
complex -- something a single whole-tree scan cannot fully answer alone.

Your primary goal is to produce a complete answer while minimizing
specialist invocations. Every specialist call has a cost (latency, tokens,
rate limits). Prefer the smallest plan that completely answers the concern.
You have a hard budget of {max_specialist_calls} specialist calls, with at
most {max_parallel_calls} running concurrently.

Available specialists (call via the task tool):
{roster}

Before delegating any work:
1. Identify the information required to answer the concern.
2. Determine the minimum set of specialists needed.
3. Prefer whole-project specialists over package-level specialists.
4. Assume the concern is solved after each specialist completes.
5. Only continue if there is a concrete information gap.

Whole-project specialists:
- vulnerability_agent covers vulnerabilities for every dependency.
- license_agent covers licensing for every dependency.
Each scans the ENTIRE dependency tree in a single run -- delegate to each at
most once. If either fully answers the concern, do not invoke additional
specialists to validate or expand those findings.

Before dispatching another specialist, ask: "What new information will this
specialist provide that is necessary for the final report?" If the answer is
"none" or "only confirmation", stop instead.

Do not collect evidence simply because it may be interesting. Only collect
evidence required to answer the user's concern.

Never use multiple specialists to answer the same question unless the
previous specialist explicitly left an information gap. For example:
vulnerability_agent finds known CVEs, then web_research_agent finds the same
CVEs from GitHub advisories -- this should never happen.

For every package-scoped specialist you do use, make sure your delegated
tasks collectively cover every direct dependency relevant to the concern --
you may be asked to cover specific missing ones if you stop early.

The investigation is complete when:
- every required question has evidence;
- no remaining evidence gap exists;
- additional specialists would only increase confidence rather than change
  conclusions.
At that point, stop.

If answering the concern would exceed your execution budget, prioritize the
highest-risk dependencies first and report which packages remain unanalyzed.

Direct dependencies (name@installed_version): {direct_deps}
Concern: {concern} (type={concern_type}, scope={concern_scope})
Project context: {context}
```

`concern_type`/`concern_scope` are read from `state["structured_concern"]`
and interpolated alongside the existing raw `concern` text.

### 8. Complex path — conditional `coverage_gate` enforcement

`coverage_gate` (`deepagent/nodes.py:174`) currently forces per-direct-dependency
coverage unconditionally whenever the complex path is taken (the existing D5
coverage guarantee). That guarantee has a real cost `backstop_dispatch`
doesn't respect: `backstop.py`'s loop over `missing_deps` has **no** budget
or concurrency cap — it forcibly runs a specialist for every remaining
missing direct dependency regardless of `DeepAgentLimits`, which would
silently defeat both the new call budget and the rewritten prompt's
"prioritize highest-risk, report unanalyzed" instruction for any concern
that isn't explicitly asking for exhaustive per-dependency treatment.

`structured_concern.requires_per_dependency_analysis` already exists to
answer exactly this question, so `coverage_gate` reads it before doing any
other work:

```python
async def coverage_gate(state: AnalysisState, config: RunnableConfig) -> dict:
    concern = Concern(**state["structured_concern"])
    if not concern.requires_per_dependency_analysis:
        # This complex concern wasn't asking for exhaustive per-package
        # treatment -- trust the deep agent's own prioritization (see the
        # rewritten prompt) instead of forcing full coverage.
        return {
            "missing_deps": [],
            "correction_rounds": (state.get("correction_rounds") or 0) + 1,
        }
    # ... existing body: whole_tree_scan_satisfies_concern judge +
    # compute_missing_direct_deps, unchanged.
```

`route_after_coverage_gate` needs no changes — it already routes straight to
`save_analysis_result` whenever `missing_deps` is empty, which is exactly
what this early return produces. No new graph edges, no new nodes: this is
a change to `coverage_gate`'s body only, and it also saves a
`whole_tree_scan_satisfies_concern` LLM call in this case.

If `requires_per_dependency_analysis` is somehow missing from
`state["structured_concern"]` (shouldn't happen — `understand_concern`
always runs first — but defensively), default to `True`: the conservative
direction, matching every other LLM-judge fallback in this codebase (a
spurious forced-coverage costs extra calls, never a missed one).

## Error handling

- `understand_concern`'s structured-output call fails (exception) ->
  propagates and fails the job, same as any other required LLM call in this
  subgraph today (e.g. `_extract_dispatch` has no fallback either). No new
  silent-failure mode introduced.
- `run_direct_agents`: if one `preferred_agents` entry's `agent_class().run()`
  raises, that failure propagates (same as today's behavior for the deep
  agent path — `subagent_wrapper._run` has no try/except around
  `agent_class().run()` either). Not changing that convention here.
- Budget-exhausted subagent calls in `subagent_wrapper._run`: return
  gracefully (no exception), just skip running the specialist — this is
  normal operation, not an error path.

## Testing

- `tests/unit/subgraphs/analysis/test_concern.py` (new): `Concern` model
  validation, `is_simple` truth table — every `SIMPLE_CONCERN_TYPES` subset
  combination, `requires_per_dependency_analysis=True` forces complex,
  `scope="specific_packages"` forces complex, a `type` outside
  `SIMPLE_CONCERN_TYPES` forces complex.
- `tests/unit/subgraphs/analysis/nodes/test_understand_concern.py` (new):
  structured-output call gets the right roster/direct-deps context; result
  round-trips into `state["structured_concern"]` as a dict.
- `tests/unit/subgraphs/analysis/nodes/test_run_direct_agents.py` (new):
  single-agent and both-agents (`vulnerability_agent` + `license_agent`)
  concern dispatch correct `AgentDispatch`s, concurrent execution, correct
  `bundle_ids`/`agent_calls` shape.
- `tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py`
  (existing, extend): budget check skips the specialist call once
  `max_specialist_calls` is reached; semaphore caps concurrent
  `run_specialist` calls at `max_parallel_calls` (simulate with a slow fake
  agent and assert peak concurrency).
- `tests/unit/subgraphs/analysis/deepagent/test_coverage.py` (existing,
  extend): `coverage_gate` returns `missing_deps=[]` immediately — no bundle
  fetch, no `whole_tree_scan_satisfies_concern` call — when
  `requires_per_dependency_analysis=False`; existing behavior unchanged when
  `True`; defaults to the `True` (enforce) path when the field is absent
  from `structured_concern`.
- Graph-level test (wherever the analysis subgraph is exercised end-to-end,
  e.g. `tests/subgraphs/test_analysis_subgraph.py`): a simple concern
  (`type=["vulnerability"]`) never reaches `analysis_deepagent_node` or
  `coverage_gate`; a complex concern with `requires_per_dependency_analysis=True`
  (or `type=["maintenance"]`) still goes through the existing deep-agent path
  unchanged; a complex concern with `requires_per_dependency_analysis=False`
  reaches `save_analysis_result` straight from `coverage_gate` without
  looping back to `analysis_deepagent_node` or reaching `backstop_dispatch`,
  even with direct deps left uncovered.

## Amendment (2026-08-02): whole-tree agents always run as a prefix, never re-planned by the deep agent

### Problem

The original design only ran `vulnerability_agent`/`license_agent` directly
for concerns classified fully "simple." A *mixed* concern — e.g.
`type=["vulnerability", "maintenance"]` — was entirely "complex," so its
vulnerability portion was left to the deep agent's own LLM planning to
rediscover and dispatch, even though `vulnerability_agent` is deterministic,
ignores `packages_to_focus`, and always returns complete per-dependency
findings in one pass regardless of what else the concern asks for. The deep
agent's prompt still listed it as an available specialist and had to spend a
planning turn deciding whether to invoke it — exactly the "collect evidence
you already have" waste the router was built to eliminate.

### Goal

Whole-tree agents relevant to a concern always run directly, regardless of
whether the concern has other, non-whole-tree work too. The deep agent (if
still needed for a genuine remainder) never re-plans work already done.

```
understand_concern -> run_direct_agents (always runs; dispatches whatever of
                        vulnerability_agent/license_agent apply -- 0, 1, or 2
                        agents, never more)
                     -> route_concern (evaluated AFTER whole-tree execution)
                          "simple"  -> save_analysis_result
                          "complex" -> analysis_deepagent_node -> coverage_gate -> ...
```

`route_concern`/`is_simple` are unchanged — "simple" still means no
remainder at all (today's exact semantics). What changes is *when*
`run_direct_agents` runs: unconditionally, right after `understand_concern`,
instead of only on the simple branch. On a pure simple concern this is
behaviorally identical to before (`whole_tree_agents(concern)` equals
`concern.preferred_agents` in that case). On a mixed concern, it peels off
just the whole-tree subset before the deep agent ever starts.

### Changes

- New `concern.whole_tree_agents(concern) -> list[str]`: returns
  `concern.preferred_agents` filtered to `WHOLE_TREE_AGENT_TYPES`, or `[]` if
  `concern.scope != "all_dependencies"`.
- `run_direct_agents` dispatches `whole_tree_agents(concern)` instead of
  `concern.preferred_agents` — the only change to that node.
- `graph.py`: `understand_concern -> run_direct_agents` becomes an
  unconditional edge; the `route_concern` conditional edge moves from
  `understand_concern`'s output to `run_direct_agents`'s output, mapping to
  `{"simple": "save_analysis_result", "complex": "analysis_deepagent_node"}`.
  `coverage_gate` needs no changes — it already reads whole-tree results off
  `state["agent_calls"]` regardless of which node produced them.
- `analysis_deepagent_node`'s first-round prompt construction:
  - `_roster()` gains an `exclude` parameter; excludes whichever whole-tree
    agents are already present in `state["agent_calls"]`.
  - A new `{already_done}` template slot: `"Already completed for this
    concern: [...] -- do not dispatch these again; focus on the remaining
    investigation."` when non-empty, blank otherwise.
  - **Correctness fix required by this change**: `deepagent_state`'s own
    `bundle_ids`/`agent_calls` are now seeded from the outer
    `state["bundle_ids"]`/`state["agent_calls"]` on the first round (were
    previously always `[]`). Without this, the D8 whole-tree dedup in
    `subagent_wrapper._run` — which only checks the deep agent's *own*
    internal `agent_calls`, not the outer `AnalysisState` — would not
    recognize a whole-tree agent that ran via the prefix, so if the deep
    agent's LLM ignored the prompt's `{already_done}` note and dispatched it
    anyway, it would actually re-run (a real second Trivy scan) instead of
    hitting the existing no-op path. The existing delta-slicing logic
    (`prev_bundle_ids`/`prev_call_bundle_ids`, already built to handle
    cross-round re-emission) transparently handles the seeded entries the
    same way — no changes needed there.

### Testing

- `concern.whole_tree_agents`: returns the whole-tree subset of
  `preferred_agents`; empty when scope is `specific_packages`; empty when no
  whole-tree type is present.
- `run_direct_agents`: a mixed-concern case where `preferred_agents` includes
  a non-whole-tree agent (e.g. `maintenance_agent`) asserts that agent is
  never dispatched.
- `analysis_deepagent_node`: given a state where `agent_calls` already
  contains a whole-tree agent's record, asserts the constructed prompt
  excludes it from the roster, includes the `{already_done}` note, and that
  `deepagent_state`'s own `bundle_ids`/`agent_calls` are seeded from the
  outer state (not empty).
- Graph-level: a mixed concern (`type=["vulnerability", "maintenance"]`)
  asserts `vulnerability_agent`'s scan runs exactly once (via the prefix),
  the deep agent is invoked with `vulnerability_agent` excluded from its
  roster and the already-done note present, and the final result correctly
  combines both agents' findings without double-counting.
- Existing test fixed as a side effect: one pre-existing graph-level test
  (`test_backstop_fires_when_deep_agent_never_delegates`) used a
  `type=["vulnerability"]` concern that, under this amendment, would now
  trigger a real (unmocked) whole-tree prefix dispatch — its concern was
  changed to `type=["maintenance"]` to preserve the test's actual intent
  (proving the backstop mechanism) without now-irrelevant entanglement with
  the whole-tree prefix. No other existing test needed changes; in
  particular `test_coverage_gate_skips_per_package_coverage_when_whole_tree_scan_satisfies_concern`
  passed unmodified, confirming the seeding fix works correctly for the
  pre-existing (non-mixed) whole-tree-via-deep-agent scenario too.

### Follow-up: simplify and fully dynamize the prompt (same day)

The static "Whole-project specialists" paragraph in `_SYSTEM_TEMPLATE`
unconditionally described `vulnerability_agent`/`license_agent` as available
even when the roster right above it had just excluded them (already run via
the prefix) — contradictory, and dead weight when both had run. Removed
entirely: each agent's own roster description (`get_agent_descriptions()`)
already states it's a whole-tree, single-pass scan, and `{already_done}`
already covers what's done and why not to re-dispatch it — no separate
paragraph needed, dynamic or otherwise.

Reviewing the whole prompt for that fix surfaced two more now-stale/
redundant pieces, folded away in the same pass:
- The 5-step numbered "Before delegating any work" planning phase — step 3
  ("prefer whole-project specialists over package-level specialists") no
  longer makes sense now that whole-project specialists are peeled off
  before the deep agent ever starts. The whole numbered list collapsed into
  the single justification-before-dispatch rule ("what new information will
  this provide that's necessary for the final report?").
- The standalone "do not collect evidence simply because it may be
  interesting" sentence folded into that same rule's answer list rather than
  standing alone as a separate paragraph.

No test changes were needed beyond the prompt text itself — all prior
assertions (budget numbers, `type=`/`scope=` interpolation, roster exclusion,
the `{already_done}` note) still hold against the shorter prompt.

## Amendment (2026-08-02, second): reject non-dependency concerns; end the whole job cleanly

### Problem

Two gaps identified by inspecting the graph wiring: (1) `run_direct_agents`
runs unconditionally and did an unconditional DAO fetch (`get_prep`) even
when `whole_tree_agents(concern)` is empty — any pure maintenance/
supply_chain/web_research concern paid for a DB round-trip whose result was
never used; (2) nothing validated that `understand_concern`'s LLM output
reflected an actual dependency-risk request. For invalid input (a greeting,
small talk, unrelated question), the classifier was still forced to emit a
syntactically valid `Concern` — depending on what it guessed, the job would
silently produce an empty "successful" analysis, or worse, run a real
whole-tree scan for input that was never a concern at all. Nothing rejected
or explained this to the caller.

### Goal

1. `run_direct_agents` returns before touching the DAO when there is nothing
   to dispatch.
2. An unrecognizable concern is rejected before any specialist or the deep
   agent ever runs, the job is marked **done** (not failed) with an
   explanation, and remediation/report are skipped entirely for that job —
   not just the analysis subgraph.

### Changes

- `run_direct_agents`: computes `whole_tree_agents(concern)` first and
  returns `{"bundle_ids": [], "agent_calls": []}` immediately if empty,
  before calling `get_services`/`get_prep`.
- `Concern` gains `is_valid: bool` as a **required field with no default**.
  A default was deliberately rejected: with `with_structured_output(...,
  method="function_calling")`, a field with a Python-level default is
  typically marked non-required in the JSON schema sent to the model, so an
  uncertain model could simply omit it and silently get `True` — exactly
  the failure mode this field exists to prevent. `understand_concern`'s
  classifier prompt gained a rule: set `is_valid=false` for anything that
  isn't a dependency-risk concern, with fixed placeholder values for the
  other required fields in that case (`type=["other"]`,
  `scope="all_dependencies"`, `packages=[]`,
  `requires_per_dependency_analysis=false`, `preferred_agents=[]`) since
  they're never read downstream when invalid.
- New router `concern.route_after_understand_concern(state) -> "valid" |
  "invalid"`, reading `state["structured_concern"].get("is_valid", True)`
  (defaults to the conservative/enforcing direction, matching every other
  defensive default already in this subgraph). Wired as the **first**
  conditional edge out of `understand_concern` — before `run_direct_agents`,
  so nothing runs for a rejected concern.
- New terminal node
  `nodes/handle_invalid_concern.handle_invalid_concern`: writes
  `INVALID_CONCERN_MESSAGE` via `job_repo.update_artifact_data(job_id,
  ANALYSIS, {"message": ...})` — the same per-node artifact-data channel
  `save_analysis_result` already uses for `agent_calls`, so no new
  frontend-facing contract was introduced — and returns `{}` (no
  `analysis_result_id`). Edges straight to the subgraph's own `END`.

  New graph shape:
  ```
  understand_concern -> route_after_understand_concern
                           "invalid" -> handle_invalid_concern -> END
                           "valid"   -> run_direct_agents -> route_concern -> ...
  ```

- **No changes to `main_graph.py` or `job_runner.py`.** This is the load-
  bearing design decision: `main_graph.py`'s `_after_analysis` already
  contains `if not state.get("analysis_result_id"): return END`, which
  today handles the case where `AnalysisState` never produces a result at
  all — ending `main_graph` immediately, skipping `REMEDIATION` and
  `REPORT`. `job_runner.py`'s `_finalize` already treats that early `END`
  as a normal completion (`report_result_id`/`remediation_result_id` empty
  strings, then `JobStatus.done`) rather than `mark_failed` — that branch
  only fires for `discovery_error` / missing `prep_result_id`, both PREP-
  stage conditions unrelated to this. By simply never setting
  `analysis_result_id`, `handle_invalid_concern` reuses both mechanisms
  exactly as-is: the job ends as `done`, with the whole rest of the
  pipeline skipped, with zero new state fields or routing logic anywhere
  outside the analysis subgraph.

### Testing

- `run_direct_agents`: asserts `get_services` is never called when
  `whole_tree_agents(concern)` is empty.
- `concern.route_after_understand_concern`: valid, invalid, and
  missing-field-defaults-to-valid cases.
- `handle_invalid_concern`: asserts the artifact-data call and that no
  `analysis_result_id` key is returned.
- Graph-level: an `is_valid=False` concern reaches the subgraph's `END`
  without `analysis_result_id` set and without `run_direct_agents` or
  `analysis_deepagent_node` ever running (proven via
  `AssertionError`-raising mocks on both, the same technique already used
  for the simple/complex router tests).
