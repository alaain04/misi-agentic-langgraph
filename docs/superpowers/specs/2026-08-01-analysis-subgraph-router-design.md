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
understand_concern -> route_concern
                         |-- simple  --> run_direct_agents --> save_analysis_result
                         `-- complex --> analysis_deepagent_node --> coverage_gate --> ... (unchanged)
```

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
