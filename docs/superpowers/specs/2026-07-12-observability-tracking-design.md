# Observability: Agent Calls, Per-Agent Timing, Per-Subgraph Cost

**Date:** 2026-07-12
**Status:** Approved

## Overview

Today the pipeline has no visibility into what happened *inside* the analysis
subgraph's dispatch loop, and cost is only tracked once for the entire job.
Specifically:

- **No record of which agents ran, in which conductor iteration, or which
  tools each agent invocation used.** `domain_agent` runs an agent and saves
  its `EvidenceBundle`, discarding everything about the run itself (tool
  calls, iteration count).
- **Subgraph-level `started_at`/`completed_at` already exists** —
  `job_runner._stream_graph` calls `start_artifact`/`complete_artifact` for
  PREP, ANALYSIS, and REPORT today. What's missing is **per-agent** timing
  inside the analysis subgraph.
- **Cost is tracked once, for the whole job.** `CostCallback` accumulates
  token cost across every LLM call in the run; `job_runner` saves the total
  to the job document at the end. There is no per-subgraph breakdown.

This design adds: (1) an agent-call log with per-agent timing, scoped to the
analysis subgraph, and (2) per-subgraph cost for all three subgraphs.

### Non-goals

- Discovery and report subgraphs do not get an agent-call log — neither has
  a conductor/dispatch loop like analysis, so the concept doesn't apply.
  Equivalent per-node logging there is a separate future increment if needed.
- No frontend surface. This is backend data model + persistence only; the
  execution-graph viz can consume it later.
- No change to job-level total cost (`dao.save_cost` at job completion stays
  as-is).

---

## Part 1: Agent-call log + per-agent timing (analysis subgraph)

### 1. New model: `AgentCallRecord`

Added to `src/models/results.py`, alongside `EvidenceBundle`:

```python
class AgentCallRecord(BaseModel):
    conductor_iteration: int
    agent_type: str
    domain: str
    tools_used: list[str]   # tool names, in call order, across all react-loop rounds
    react_iterations: int    # react-loop rounds taken (1 for deterministic agents)
    started_at: str
    finished_at: str
    bundle_id: str
```

Kept separate from `EvidenceBundle` deliberately: `EvidenceBundle` is domain
evidence persisted via `result_dao`; `AgentCallRecord` is execution telemetry
persisted via the job's artifact. They have different consumers and different
lifetimes — mixing them would couple two unrelated persistence paths.

### 2. `BaseAgent.run()` contract change

Both implementations of `run()` — the default in `base_agent.py` and the
override in `vulnerability_agent.py` — change return type from `EvidenceBundle`
to `tuple[EvidenceBundle, list[str], int]` (`bundle, tools_used, react_iterations`):

- `_react_loop` (`base_agent.py`) already has `tool_results` and the
  iteration counter in scope. Surface `[tr.tool for tr in tool_results]` and
  `iteration + 1` (the round at which it broke) instead of discarding them.
- `VulnerabilityAgent.run()` is deterministic (one `npm_audit` call, no
  react loop) and returns `(bundle, ["npm_audit"], 1)`.

### 3. `domain_agent.py` node

Wraps the call with timestamps, builds the record, returns it alongside
`bundle_ids`:

```python
started_at = datetime.now(UTC).isoformat()
bundle, tools_used, react_iterations = await agent.run(dispatch, prep)
finished_at = datetime.now(UTC).isoformat()
bundle_id = await dao.save_bundle(bundle)

record = AgentCallRecord(
    conductor_iteration=state["conductor_iteration"],
    agent_type=dispatch.agent_type,
    domain=dispatch.domain,
    tools_used=tools_used,
    react_iterations=react_iterations,
    started_at=started_at,
    finished_at=finished_at,
    bundle_id=bundle_id,
)
return {"bundle_ids": [bundle_id], "agent_calls": [record.model_dump()]}
```

`state["conductor_iteration"]` is already visible here: `_after_conductor`
(`analysis/graph.py`) fans out via `Send("domain_agent", {**state, ...})`,
which carries the full state — including the iteration number the conductor
just set — into every parallel `domain_agent` invocation.

### 4. `AnalysisState` — new field

```python
agent_calls: Annotated[list[dict], operator.add]
```

Same accumulation pattern as `bundle_ids` today: each parallel `domain_agent`
`Send` contributes its own entry, and entries accumulate across conductor
loop iterations without any node needing to read-then-write the whole list.

### 5. `save_analysis_result.py` — flush on subgraph exit

This node already runs once, at the end of the analysis subgraph. Add one
write, using the job repo already available via `PipelineConfigurable`:

```python
job_repo = get_services(config)["job_repo"]
await job_repo.update_artifact_data(
    state["job_id"], ANALYSIS, {"agent_calls": state.get("agent_calls") or []}
)
```

No new DAO method — `update_artifact_data` already merges arbitrary fields
into an existing artifact entry.

---

## Part 2: Per-subgraph cost

`job_runner.py` already streams subgraph completions through
`_stream_graph` in order (PREP, then ANALYSIS, then REPORT), each calling
`dao.complete_artifact`. Track a running snapshot of `cost_cb.cost()` and
diff at each of those existing boundary points:

```python
async def _stream_graph(graph, input_data, config, dao, job_id, cost_cb) -> None:
    prev_cost = 0.0
    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            ...
            if node_name in (PREP, ANALYSIS, REPORT):
                cost_now = cost_cb.cost()
                await dao.update_artifact_data(
                    job_id, node_name, {"cost": round(cost_now - prev_cost, 6)}
                )
                prev_cost = cost_now
```

This requires passing `cost_cb` into `_stream_graph` (currently only used at
the call sites in `run_analysis`/`resume_analysis`) and no changes at all to
`CostCallback` or graph state — cost is a callback concern, not a state
concern, and diffing cumulative totals at existing boundaries is sufficient
because the three subgraphs run strictly sequentially, never concurrently.

The final `dao.save_cost(job_id, cost_cb.cost())` call at job completion is
unchanged (still the job-level total).

---

## Data Flow

```
analysis_conductor (iteration N) → _after_conductor → Send × k → domain_agent
                                                                     │
                                              agent.run() → (bundle, tools_used, react_iterations)
                                                                     │
                                       AgentCallRecord{iteration=N, ...} ─┐
                                                                          │
                                    {"bundle_ids": [...], "agent_calls": [record]}
                                                                          │
                                          (accumulates via operator.add across
                                           all Sends and all conductor iterations)
                                                                          │
                                                              save_analysis_result
                                                                          │
                            job_repo.update_artifact_data(job_id, ANALYSIS, {"agent_calls": [...]})

job_runner._stream_graph:
  PREP done  → cost snapshot diff → artifacts[PREP].cost
  ANALYSIS done → cost snapshot diff → artifacts[ANALYSIS].cost
  REPORT done → cost snapshot diff → artifacts[REPORT].cost
```

---

## Error Handling

- No new error handling. If `agent.run()` raises, the node fails exactly as
  it does today — no partial `AgentCallRecord` is created, matching the
  existing behavior for `bundle_ids`.
- Cost diffing has no failure mode of its own: `cost_cb.cost()` is a pure
  in-memory accumulator, and `update_artifact_data` already tolerates being
  called for an existing artifact entry.

---

## Testing

Unit (`tests/unit/`):
- `base_agent._react_loop` (via a concrete test agent): returned tuple's
  `tools_used` matches the tool names actually invoked, in order;
  `react_iterations` matches the round at which the loop broke (finalize or
  critique acceptance).
- `VulnerabilityAgent.run()` returns `(bundle, ["npm_audit"], 1)`.

Subgraph (`tests/subgraphs/test_analysis_subgraph.py`):
- After a run with 2 conductor iterations and parallel dispatches, the final
  state's `agent_calls` has one entry per `domain_agent` invocation, each
  tagged with the correct `conductor_iteration`.
- `save_analysis_result` calls `update_artifact_data` with the accumulated
  `agent_calls` list.

Unit (`tests/unit/` for `job_runner`):
- Given a sequence of fake `cost_cb.cost()` return values across PREP/
  ANALYSIS/REPORT boundaries, `update_artifact_data` is called with the
  correct per-subgraph diff (not the cumulative total) for each node.

---

## Summary of Changes

| File | Change |
|------|--------|
| `src/models/results.py` | New `AgentCallRecord` model |
| `analysis/agents/base_agent.py` | `run()`/`_react_loop` return `(EvidenceBundle, tools_used, react_iterations)` |
| `analysis/agents/vulnerability_agent.py` | `run()` returns `(bundle, ["npm_audit"], 1)` |
| `analysis/nodes/domain_agent.py` | Times the call, builds `AgentCallRecord`, returns `agent_calls` |
| `analysis/state.py` | New field `agent_calls: Annotated[list[dict], operator.add]` |
| `analysis/nodes/save_analysis_result.py` | Flushes `agent_calls` to the ANALYSIS artifact via `update_artifact_data` |
| `services/job_runner.py` | `_stream_graph` diffs `cost_cb.cost()` at each subgraph boundary, writes per-subgraph cost |
| `tests/unit/`, `tests/subgraphs/` | Coverage per Testing section |
