# Report Subgraph Per-Finding Agents Design

**Date:** 2026-07-18
**Status:** Approved

## Overview

Replace the report subgraph's single shared ReAct conductor
(`report_conductor` ↔ `report_tool_runner` ↔ one big `save_report_result` LLM
call over every finding at once) with the same architecture the analysis
subgraph already uses: a deterministic dispatcher that fans out one isolated,
fresh-context subagent per finding, each running its own bounded ReAct +
self-critique loop, followed by a light synthesis step.

Today, `report_conductor` gathers `web_search`/`blast_radius`/`code_impact`
results for *all* findings into one shared `tool_results` list, and
`save_report_result` makes a single LLM call over all findings' evidence at
once to write the whole `ReportResult`. Because one LLM call reasons over
every finding's evidence simultaneously, `save_report_result.py` has
accumulated deterministic guardrails to patch cross-finding bleed:
`_drop_mismatched_evidence` (evidence attributed to the wrong package),
`_group_enrichment_by_dep`'s heuristic substring attribution, and an
alternatives filter that excludes packages flagged elsewhere in the same
report. These are symptom patches for a root cause — shared context — not
fixes.

The analysis subgraph solves the equivalent problem for evidence-gathering
already: `analysis_conductor` (deterministic/LLM dispatch) fans out to
`domain_agent` via `Send`, each running `base_agent._react_loop` in total
isolation, with an in-loop self-critique (`critique_findings`, see
[[2026-07-12-agent-self-correction-design]]) before the agent's findings are
accepted. Mirroring this for report enrichment removes the root cause
structurally: a subagent that only ever sees one finding's own dep_name and
tool results cannot misattribute evidence across findings, because there is
no other finding in its context to misattribute from.

### Targeted failure modes

- **Evidence misattribution** — a finding's `evidence`/`business_impact`
  citing another package's web_search hit or blast-radius data. Prevented
  structurally (tool calls are pinned to the dispatch's own `dep_name`), not
  just filtered after the fact.
- **Self-contradictory alternatives** — recommending a package as an
  alternative when that package itself has a finding elsewhere in the same
  report. Still requires *some* cross-finding awareness (see Approach).
- **Untrusted/unsupported enrichment** — a finding whose business_impact or
  evidence isn't actually grounded in what the tools returned. Currently
  invisible; the LLM can invent plausible-sounding text. Made visible via a
  critique step and a `trust`/`observation` flag on the finding.

## Approach

**Mirror the analysis subgraph pattern exactly** — this was evaluated against
two alternatives and rejected them:

1. *Keep patching the shared loop* — rejected; treats symptoms of the shared-
   context problem, not the cause, and this project has already had to
   re-fix this class of bug once (see
   `bugfix_report_subgraph_refixed_on_main` in memory).
2. *Batch conductor reflection* — a top-level LLM conductor reviews all
   per-finding drafts together and re-dispatches with feedback. Rejected:
   reintroduces a shared-context step, and doesn't match how the analysis
   subgraph actually works today (its conductor never sees raw findings,
   only bundle summaries — see Non-goals).

**Dispatch stays deterministic, not LLM-driven.** Unlike
`analysis_conductor`, which adaptively decides *which* agents to dispatch
across multiple rounds based on evolving evidence, report enrichment doesn't
need adaptive coverage decisions — every severity-filtered finding from the
analysis phase gets exactly one enrichment pass. So `report_intake` is a
plain function (no LLM), analogous in spirit to the currently-unused
`agent_dispatcher.py` fan-out helper in analysis, not to `analysis_conductor`
itself.

**Self-critique lives inside each subagent**, exactly mirroring
`base_agent._react_loop`'s finalize gate: when a `finding_enricher` subagent
decides to finalize, `critique_report_finding` checks the draft against that
subagent's own tool results before it's accepted. On rejection with budget
remaining, feedback is injected and the subagent retries with tool access
(same mechanic as `critique_findings`/`_feedback_result` today). On rejection
after the budget is exhausted, the finding is kept — not dropped — with
`trust=False` and `observation=<critique feedback>` (this project's variant
of [[2026-07-12-agent-self-correction-design]]'s `verification_note`, but
surfaced on the finding itself since report findings are the terminal
output, unlike evidence bundles which still feed a conductor).

**One deliberate, minimal exception to isolation.** The alternatives-
exclusion rule (§ Targeted failure modes) genuinely needs to know about
*other* findings — a name only, not their evidence or descriptions.
`report_intake` computes `all_flagged_dep_names` once (the `dep_name`s of
every finding entering enrichment) and passes it read-only into every
`finding_enricher`'s prompt, purely so it can avoid suggesting one of them as
an alternative. No evidence, business_impact, or description crosses between
findings — only this one name list.

## Components

### 1. `report_intake` (new node, no LLM)

`report/nodes/report_intake.py`. Fetches `AnalysisResult` via
`dao.get_analysis`, applies the existing `filter_by_min_severity` util
(`src/utils/severity.py`) against `settings.risk_min_severity` — moved here
from `save_report_result`, so findings below threshold never enter
enrichment at all (an efficiency win: today they get a full enrichment pass
via the big LLM call and are filtered out afterward). Computes
`all_flagged_dep_names` from the filtered set. Writes both into state.

### 2. `finding_enricher_agent` (new)

`report/agents/finding_enricher_agent.py`, mirrors `base_agent._react_loop`
structurally: bounded ReAct loop, structured `FindingEnrichmentDecision`
output per iteration, self-critique finalize gate. Differences from
`base_agent`:

- Single fixed tool set per call: `web_search`, `blast_radius`,
  `code_impact` — no `agent_type` registry, since there's only one
  enrichment behavior.
- Each tool's `package_name` argument is **force-injected** to the
  dispatch's own `dep_name` inside the tool-runner, regardless of what the
  structured decision passes — mirroring how `base_agent._run_tool` already
  force-injects `repo_path`/`docker_image`/`container` via signature
  inspection. This is the structural fix: a subagent cannot fetch evidence
  for a different package even if it tried.
- System prompt includes `all_flagged_dep_names` for the alternatives-
  exclusion instruction only.
- Produces a `ReportFinding` draft (recommendation, alternatives,
  business_impact, affected_files, evidence) directly — this subsumes what
  the old `save_report_result`'s big LLM call used to do per finding.

### 3. `report/agents/critique.py` (new, mirrors `analysis/agents/critique.py`)

`critique_report_finding(original: FindingNote, draft: ReportFinding,
tool_results) -> FindingVerdict` (`ok`, `feedback`,
`calibrated_confidence`, same shape as `FindingsVerdict`). Validates: draft
evidence actually references the finding's own tooling output; business_impact
is grounded in blast_radius/code_impact output, not invented; alternatives
are backed by a web_search result and don't appear in `all_flagged_dep_names`;
severity/dep_name are unchanged from the original `FindingNote`.

### 4. `finding_enricher` node (new, mirrors `domain_agent.py`)

`report/nodes/finding_enricher.py`. Reads `state["current_finding"]` and
`state["all_flagged_dep_names"]`, loads `prep` via `dao.get_prep`, calls the
agent, appends the resulting `ReportFinding` dict (with `trust`/`observation`
set) to `enriched_findings`.

### 5. `enrichment_collector` (new, no-op fan-in)

`report/nodes/enrichment_collector.py`, a direct copy of
`evidence_collector.py`'s pattern — exists only to give LangGraph a join
point before `report_synthesizer`.

### 6. `report_synthesizer` (rewrite of `save_report_result.py`)

Takes `state["enriched_findings"]` (already vetted, trust-flagged
`ReportFinding`s) and makes one LLM call for `executive_summary` and
top-level `recommendations` only — it no longer generates or touches
per-finding evidence, business_impact, or alternatives, so
`_drop_mismatched_evidence` and `_group_enrichment_by_dep` are deleted
entirely (the problem they patched can't occur anymore).
`overall_risk_level` stays a deterministic max-severity calculation exactly
as today. Saves via the existing `dao.save_report`.

### 7. Schema changes (`src/models/results.py`)

```python
class ReportFinding(BaseModel):
    ...
    trust: bool = True
    observation: str = ""

class FindingEnrichmentDecision(BaseModel):
    tool_calls: list[ToolCall]
    finding: ReportFinding | None
    finalize: bool
    reasoning: str
```

`ReportConductorDecision` is deleted (no longer used by anything).

### 8. Deleted

`report/nodes/report_conductor.py`, `report/nodes/report_tool_runner.py`
(including its `get_findings` tool — no longer needed since `report_intake`
fetches findings once, upfront).

### 9. Graph wiring (`report/graph.py`, `report/state.py`)

```
START -> report_intake
      -> conditional edge (_dispatch_findings): list[Send] over
         findings_to_enrich, or straight to report_synthesizer if empty
              finding_enricher (fan-out, one per finding)
      -> enrichment_collector (fan-in)
      -> report_synthesizer
      -> END
```

`ReportState` drops `conductor_decision`/`tool_results`/`conductor_iteration`,
gains `findings_to_enrich: NotRequired[list[dict]]`,
`current_finding: NotRequired[dict]`,
`all_flagged_dep_names: NotRequired[list[str]]`,
`enriched_findings: Annotated[list[dict], operator.add]`. Directly parallel
to `AnalysisState`'s `current_dispatch`/`bundle_ids` shape.

## Data Flow

```
report_intake:
    analysis = dao.get_analysis(...)
    findings_to_enrich = filter_by_min_severity(analysis.findings, risk_min_severity)
    all_flagged_dep_names = [f.dep_name for f in findings_to_enrich]
        │
        ▼ (conditional edge, list[Send] fan-out — empty list routes straight to synthesizer)
finding_enricher × N (parallel, isolated):
    dispatch = FindingNote(**current_finding)
    loop (bounded):
        LLM decision: tool_calls | draft ReportFinding + finalize
        tool_calls run with package_name force-pinned to dispatch.dep_name
        on finalize: critique_report_finding(dispatch, draft, tool_results)
            ok            -> trust=True,  observation=""
            not ok, budget remains -> inject feedback, loop again
            not ok, budget exhausted -> trust=False, observation=<feedback>
        │
        ▼ enriched_findings += [draft]
        ▼
enrichment_collector (fan-in, no-op)
        │
        ▼
report_synthesizer:
    LLM: executive_summary + recommendations over enriched_findings
    overall_risk_level = max severity of enriched_findings (deterministic)
    dao.save_report(ReportResult(...))
```

## Error Handling

- Critique failure after retries exhausted: finding kept with
  `trust=False, observation=<feedback>` — never dropped (per explicit
  decision; a report writer that silently loses risk findings is worse than
  one that shows a flagged, lower-confidence finding).
- Tool failures inside a subagent's loop: same tolerant pattern as
  `base_agent._run_tool` — caught, returned as an error `ToolResult`, loop
  continues.
- `critique_report_finding` raising/timing out: treated as a pass (`ok=true`)
  using the subagent's own draft, same tolerant fallback as
  `critique_findings` today (a critic failure must not fail the report).
- Empty `findings_to_enrich`: `_dispatch_findings` routes straight to
  `report_synthesizer`, which produces an empty-findings report exactly as
  today (`overall_risk_level="none"`).
- Frontend/artifact contract: verified via `job_runner.py` that
  `start_artifact`/`complete_artifact` are keyed only at the top-level
  `REPORT` constant, not on internal subgraph node names — this internal
  restructuring requires **no frontend changes**.

## Testing

Unit (`tests/unit/`):
- `finding_enricher_agent`: happy path produces a `ReportFinding` with
  `trust=True`; critique-fail-then-retry-succeeds; critique-fail-after-
  max-retries produces `trust=False` with `observation` set to the critique
  feedback.
- Tool-runner force-injection: a tool call with a different `package_name`
  than the dispatch's `dep_name` still executes against the dispatch's own
  `dep_name` (the structural-fix assertion).
- `critique_report_finding`: rejects a draft whose evidence doesn't
  reference the finding's own tool results; rejects business_impact not
  grounded in blast_radius/code_impact output; rejects an alternative that
  appears in `all_flagged_dep_names`.
- `_dispatch_findings` routing: empty `findings_to_enrich` → routes to
  `report_synthesizer`; N findings → N `Send`s, each carrying the right
  `current_finding`.
- `report_intake`: severity filtering behavior (replaces the equivalent
  assertions currently in `test_save_report_result.py`).

Subgraph (`tests/subgraphs/test_report_subgraph.py`, rewritten to mirror
`test_analysis_subgraph.py`'s mocking approach — mock `finding_enricher_agent`'s
`_llm` and `critique_report_finding`, seed `AnalysisResult`, run
`build_report_subgraph()` end to end):
- Blast-radius grounding via codegraph still overwrites `affected_files`
  from real tool output (equivalent of today's
  `test_report_grounds_blast_radius_via_codegraph`).
- Severity filtering drops findings before enrichment (equivalent of
  `test_report_drops_findings_below_min_severity`), asserted at
  `report_intake`'s output this time, not post-hoc.
- Alternative that is itself a flagged dependency is excluded (equivalent of
  `test_report_strips_alternative_that_is_itself_a_flagged_dependency`), now
  via the `all_flagged_dep_names` prompt mechanism rather than a deterministic
  post-filter.
- A finding whose evidence fails critique twice ends up in
  `ReportResult.findings` with `trust=False` and a non-empty `observation`,
  not dropped.

## Non-goals

- **Adaptive/multi-round dispatch** for report enrichment (à la
  `analysis_conductor`'s iterative coverage decisions) — every
  severity-filtered finding gets exactly one enrichment pass; no gap-driven
  re-dispatch loop at the top level.
- **Batch conductor reflection** — considered and rejected in Approach.
- **Dropping untrusted findings** — considered and rejected; they're kept
  with `trust=False` instead.
- **Persisting per-finding evidence bundles to a new Mongo collection** —
  unlike analysis's `EvidenceBundle`s (read back across conductor
  iterations), enrichment results only need to live in `ReportState` for the
  duration of one subgraph run; no cross-iteration persistence is needed.
- **Frontend changes** — verified not required (see Error Handling).

## Summary of Changes

| File | Change |
|------|--------|
| `report/nodes/report_intake.py` | New: fetch + severity-filter findings, compute `all_flagged_dep_names` |
| `report/agents/finding_enricher_agent.py` | New: bounded ReAct + self-critique loop per finding, mirrors `base_agent._react_loop` |
| `report/agents/critique.py` | New: `critique_report_finding` + `FindingVerdict`, mirrors `analysis/agents/critique.py` |
| `report/nodes/finding_enricher.py` | New: node wrapper, mirrors `domain_agent.py` |
| `report/nodes/enrichment_collector.py` | New: no-op fan-in, mirrors `evidence_collector.py` |
| `report/nodes/save_report_result.py` | Rewritten into `report_synthesizer`: light LLM call (executive_summary/recommendations only) + deterministic `overall_risk_level`; `_drop_mismatched_evidence`/`_group_enrichment_by_dep` deleted |
| `report/nodes/report_conductor.py` | Deleted |
| `report/nodes/report_tool_runner.py` | Deleted |
| `report/graph.py` | Rewritten: `report_intake` → `Send` fan-out → `finding_enricher` → `enrichment_collector` → `report_synthesizer` |
| `report/state.py` | `ReportState` reshaped per Data Flow |
| `src/models/results.py` | `ReportFinding.trust`/`.observation` added; `FindingEnrichmentDecision` added; `ReportConductorDecision` deleted |
| `tools/external_api.py` | `make_web_search_tool(dep_name)` factory added, mirrors `make_blast_radius_tool`/`make_code_impact_tool` |
| `tests/unit/`, `tests/subgraphs/` | Coverage per Testing section |
