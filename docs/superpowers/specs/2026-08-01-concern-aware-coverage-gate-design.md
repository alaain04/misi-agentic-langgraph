# Concern-aware coverage gate for the analysis deep agent

## Problem

The analysis deep agent's coverage guarantee (spec D5) requires every direct
dependency to be looked at by a *package-scoped* agent (`maintenance_agent`,
`supply_chain_agent`, `web_research_agent`) in addition to whichever
*whole-tree* agents (`vulnerability_agent`, `license_agent`) already scanned
the entire tree in one run. `coverage_gate` enforces this unconditionally:
`compute_missing_direct_deps` (`deepagent/coverage.py`) doesn't look at the
concern at all, so a direct dep with no package-scoped `AgentCallRecord`
always counts as missing, even when a whole-tree scan already fully answered
the concern.

Confirmed empirically against job `6a6db91f414c989f5ecd71a9` (concern:
"analyze vulnerable dependencies", repo:
`github.com/alaain04/challenge-order-api`):

- `vulnerability_agent` ran once (Trivy, whole tree), found 36 findings
  including `mongoose medium - Prototype pollution in mongoose update
  casting via __proto__-prefixed dotted path` (GHSA-664h-wqgq-64gw).
- The coverage gate then forced package-scoped coverage of the remaining
  direct deps anyway. The deep agent picked `web_research_agent`, which ran
  5 ReAct iterations, 8 tool calls (`web_search` x4, `github_advisory` x2,
  `osv_lookup` x2) across 9 packages, and reported exactly one finding:
  `mongoose@8.14.1` GHSA-664h-wqgq-64gw prototype pollution — the *same*
  advisory Trivy already found. Net new information: zero, for a 5-iteration
  LLM loop plus real external API calls.

Root cause: nothing in the current graph ever asks "does the whole-tree scan
that already ran fully address this concern, making per-package coverage of
the rest pointless?" There is no such gate on disk today — confirmed via
`git grep --all` across every commit in this repo's history for
`whole_tree_scan_satisfies_concern`, which returns zero matches. (An earlier
codegraph query in this session surfaced a function by that name and claimed
it was "verbatim, byte-for-byte" disk content; it does not exist anywhere in
this codebase's git history. This is new code, not a restoration, and the
codegraph result for this file pair should not be trusted until reindexed.)

## Goal

Add a concern-aware gate: when the whole-tree agent(s) that have
*successfully* run already fully address the concern, skip forcing
per-package coverage of the remaining direct dependencies entirely — no
loop-back to the deep agent demanding coverage, no `backstop_dispatch`.

## Non-goals

- Not changing `backstop.py` — it's only reached via
  `route_after_coverage_gate` when `missing_deps` is non-empty, so forcing
  `missing_deps = []` upstream already prevents it from firing. No edits
  needed there.
- Not changing whether `license_agent` gets dispatched — that stays entirely
  up to the deep agent's own judgment from the roster description in its
  system prompt. This gate only controls whether *per-package* coverage is
  forced, not which whole-tree agents run.
- Not deduplicating findings across agents by advisory ID (the "cheap fix"
  option considered and rejected in favor of this one) — that would still
  pay the cost of the redundant web research, just hide it in the report.

## Architecture / data flow

`coverage_gate` (`deepagent/nodes.py:165`) currently:

```python
async def coverage_gate(state, config):
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    direct_deps = list(prep.dependency_graph.get("direct", {}).keys())
    missing = compute_missing_direct_deps(state.get("agent_calls") or [], direct_deps)
    return {"missing_deps": missing, "correction_rounds": ...}
```

New behavior:

1. From `state["agent_calls"]`, collect the `AgentCallRecord`s whose
   `agent_type` is in `WHOLE_TREE_AGENT_TYPES`.
2. Fetch their bundles (`dao.get_bundles`) and keep only the ones that
   *succeeded* — `EvidenceBundle.confidence > 0.5` (`vulnerability_agent`
   sets 0.3 on Trivy error, 1.0 on success; `license_agent` always sets 1.0).
   A bundle that's missing/unfetchable is treated as not-succeeded.
3. Build `current_roster = sorted(agent_type for each successful whole-tree
   call)`. If `current_roster` differs from `state.get("whole_tree_checked_roster")`
   and is non-empty, call the new judge function with `concern` and the
   roster's agent descriptions; cache both the roster and the verdict in
   state. If the roster hasn't grown since the last check, reuse the cached
   verdict without calling the LLM again.
4. If the cached verdict is `True`, return `missing_deps = []` (skip
   `compute_missing_direct_deps` — cheaper, and the point is exactly to not
   require per-package coverage). Otherwise, behavior is unchanged from
   today.

New function in `coverage.py` (net-new, not a restoration — see Problem
section):

```python
class _CoverageJudgment(BaseModel):
    fully_addressed: bool
    reason: str

async def whole_tree_scan_satisfies_concern(
    concern: str, ran_whole_tree_agents: list[str]
) -> bool:
    if not concern.strip() or not ran_whole_tree_agents:
        return False
    # structured-output LLM call (GPT_5_4_MINI, matching other judges in
    # this codebase), roster = ran_whole_tree_agents' descriptions from
    # get_agent_descriptions(). On any exception, return False.
```

New `AnalysisState` fields (`state.py`), both `NotRequired`, transient —
not persisted to Mongo (no `AgentCallRecord`/`EvidenceBundle` changes):

```python
whole_tree_checked_roster: NotRequired[list[str]]
whole_tree_satisfies_concern: NotRequired[bool]
```

## Error handling

- Judge LLM call raises → `fully_addressed = False` (conservative — a
  spurious False only costs extra coverage, never a missed one, matching
  every other LLM-judge convention already in this codebase).
- Bundle fetch fails or a whole-tree agent's bundle can't be found →
  excluded from the successful roster (same conservative direction).
- No whole-tree agent has successfully run yet → skip the judge call
  entirely (cheap short-circuit), behave exactly as today.

## Testing

- `tests/unit/subgraphs/analysis/deepagent/test_coverage.py` (existing file,
  currently only covers `compute_missing_direct_deps`): add cases for
  `whole_tree_scan_satisfies_concern` — concern fully covered → True;
  concern needing more (e.g. mentions "maintenance" or "supply chain") →
  False; LLM exception → False; empty concern or no whole-tree agents ran →
  False without invoking the LLM.
- Integration-level test (wherever `coverage_gate`/`route_after_coverage_gate`
  are exercised today, e.g. `test_analysis_subgraph.py`): assert that when
  the judge returns `fully_addressed=True`, `missing_deps` comes back empty
  and routing goes straight to `save_analysis_result`, never looping back to
  `analysis_deepagent_node` or reaching `backstop_dispatch`.
- Regression fixture modeled directly on job `6a6db91f414c989f5ecd71a9`:
  concern "analyze vulnerable dependencies", `vulnerability_agent` succeeds
  with findings → assert `web_research_agent`, `maintenance_agent`, and
  `supply_chain_agent` are never dispatched.
