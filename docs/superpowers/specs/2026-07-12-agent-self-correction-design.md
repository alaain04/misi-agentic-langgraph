# Agent Evidence Self-Correction Design

**Date:** 2026-07-12
**Status:** Approved

## Overview

Add an in-loop **evaluator** to each analysis domain agent that verifies its
findings against the evidence the agent itself collected, gives the agent a
chance to fix weak findings, and surfaces any unresolved concerns to the
conductor instead of letting them pass silently.

Today a `domain_agent` runs a ReAct loop and produces an `EvidenceBundle`
(findings, summary, self-assigned `confidence`). The `analysis_conductor` then
reads only each bundle's **summary + finding count** (`_format_bundles`) — never
the actual findings or the evidence behind them. So the conductor validates on
the agent's self-narrated summary and self-graded confidence. An agent that is
confidently wrong — a finding not supported by its own tool evidence — reaches
the report unchecked.

This design adds independent, per-finding verification without new graph nodes
and with a single additive schema field.

### Targeted failure modes

- **Unsupported findings** — a finding whose claim is not backed by its attached
  evidence snippets.
- **Bad confidence calibration** — self-reported confidence that does not reflect
  evidence quality.
- **Coverage gaps** — a bundle that did not actually answer its hypothesis; it
  gets a low, honest confidence and a note, which signals the conductor the
  angle is still open.

Contradictions between agents and global coverage decisions remain the
**conductor's** job. The evaluator does per-bundle, individual validation only.

---

## Approach

**Verification depth: self-consistency.** `FindingNote` already carries
`evidence: list[EvidenceRef]` (each with `tool` + `log_snippet`) — the agent
attaches the snippets it used to justify each finding. The evaluator judges each
finding's `description` + `severity` against those attached snippets. No raw
`ToolResult` persistence is required. This cannot catch a fully fabricated
snippet; that is a deliberate out-of-scope v2 (see Non-goals).

**Placement: in-loop.** The domain agent already loops
(`base_agent._react_loop`, `_MAX_ITERATIONS = 6`) with a `finalize` flag and
full tool access. The evaluator becomes a gate on `finalize` inside that loop —
no new graph node, no graph-level feedback edge, reuses the existing iteration
budget. The critic is a separate structured LLM call with its own prompt, so the
evaluation is independent even though it lives in the same module.

**Self-correction with a soft fallback.** When the critic rejects a draft:
- If iteration budget remains, its feedback is injected into the loop and the
  agent revises **with tool access** — it can collect the missing evidence and
  substantiate a real-but-underspecified finding, not merely reword it.
- If the budget is exhausted, findings are **kept** (never silently pruned), the
  bundle's confidence is set to the critic's lowered `calibrated_confidence`, and
  the unresolved critique is attached as `verification_note`. The conductor then
  decides what to do with a flagged, low-confidence bundle.

---

## Components

### 1. Evaluator: `critique_findings`

A new function (own critic system prompt, structured output) in the analysis
agents layer, e.g. `analysis/agents/critique.py`.

Signature (conceptual):

```
async def critique_findings(
    dispatch: AgentDispatch,
    findings: list[FindingNote],
) -> FindingsVerdict
```

Structured output model `FindingsVerdict`:

```python
class FindingsVerdict(BaseModel):
    ok: bool                      # all findings adequately supported
    feedback: str                 # what is unsupported / over-stated and why
    calibrated_confidence: float  # independent of the agent's self-grade
```

The critic prompt instructs: for each finding, decide whether `description` and
`severity` are supported by that finding's `evidence[].log_snippet`. Flag
findings with empty evidence, snippets that do not back the claim, or severity
that overstates the evidence. Set `ok=false` if any finding is inadequately
supported. `feedback` must be concrete and actionable (which finding, what is
missing). `calibrated_confidence` reflects overall evidence quality.

The evaluator judges only the findings and their attached evidence — it does not
call tools or re-run the investigation.

### 2. `_react_loop` finalize gate (`base_agent.py`)

Change the loop so that when the agent signals `finalize` (or the last iteration
is reached), the critic runs before the bundle is built:

```
for iteration in range(_MAX_ITERATIONS):
    decision = agent.step(...)

    if decision.finalize or iteration == _MAX_ITERATIONS - 1:
        verdict = await critique_findings(dispatch, decision.findings)
        if verdict.ok:
            confidence = verdict.calibrated_confidence
            note = None
            break
        if iteration < _MAX_ITERATIONS - 1:
            # self-correct: feed critique back, keep looping (tools available)
            tool_results.append(critique_feedback_result(verdict.feedback))
            continue
        # budget exhausted: keep findings, lower confidence, attach note
        confidence = verdict.calibrated_confidence
        note = verdict.feedback
        break

    if decision.tool_calls:
        run tools, extend tool_results
```

Notes:
- On rejection with budget remaining, the critique is injected the same way tool
  results are surfaced to the agent (via `_format_results`), so the next
  iteration sees it as feedback and can call tools to address it.
- Each rejection consumes one iteration, so the loop is naturally bounded by
  `_MAX_ITERATIONS`. No separate counter is needed.
- The final `EvidenceBundle` uses the critic's `calibrated_confidence`, not
  `decision.confidence`.

### 3. Schema: `EvidenceBundle.verification_note`

Add one additive field (`src/models/results.py`):

```python
class EvidenceBundle(BaseModel):
    ...
    confidence: float
    verification_note: str | None = None
```

`None` means the findings passed verification. A string carries the unresolved
critique for the conductor.

### 4. Conductor surfacing (`analysis_conductor.py`)

- `_format_bundles` appends the `verification_note` when present, e.g.
  `unresolved: <note>` under the bundle.
- One line added to the conductor system prompt: treat a bundle with a
  `verification_note` (and correspondingly low confidence) as an **open gap** —
  prefer to re-dispatch to close it, or discount its findings when finalizing.

No change to the conductor's structure, routing, or `AnalysisConductorDecision`.

---

## Data Flow

```
domain_agent → _react_loop:
    agent iterates (tools) ─► finalize
                                │
                     critique_findings(findings)
                     ┌──────────┴───────────┐
                    ok                     not ok
                     │              ┌────────┴────────┐
              calibrated      budget remains     budget exhausted
              confidence,     inject feedback,   keep findings,
              note=None       loop again (tools) low confidence,
                     │              │             verification_note
                     └──────┬───────┘─────────────────┘
                            ▼
                    EvidenceBundle (saved by domain_agent)
                            ▼
              evidence_collector → analysis_conductor
                    (reads verification_note + honest confidence,
                     decides re-dispatch / finalize)
```

---

## Error Handling

- If `critique_findings` raises or times out, log a warning and treat it as a
  pass (`ok=true`) using the agent's own `decision.confidence`. Verification is a
  quality gate, not a hard dependency — a critic failure must not fail the
  analysis. Follows the existing tolerant pattern in `_run_tool`.
- Empty findings: the critic is skipped; the bundle keeps confidence `0.0` /
  agent value as today.

---

## Testing

Unit (`tests/unit/`):
- `critique_findings` returns `ok=false` for a finding with empty evidence.
- `critique_findings` returns `ok=false` when a snippet does not support the
  claim; `ok=true` when it does.
- `_react_loop`: rejection with budget remaining injects feedback and iterates
  again (assert the agent is re-invoked with the feedback present).
- `_react_loop`: rejection on the last iteration keeps findings, sets
  `verification_note`, and uses `calibrated_confidence`.
- `_react_loop`: critic exception → treated as pass, agent confidence retained.

Subgraph (`tests/subgraphs/`):
- A bundle with a `verification_note` is visible to the conductor via
  `_format_bundles` (assert the note text appears in the rendered prompt input).

---

## Non-goals

- **Ground-truth verification** of snippets against raw tool output (persisting
  `ToolResult`s and checking a snippet actually appeared). Deferred; the
  self-consistency check is the incremental first step.
- **Cross-bundle contradiction detection** — stays with the conductor.
- **A separate `reflect` graph node / graph-level feedback loop** — rejected in
  favor of the in-loop evaluator (simpler, reuses budget); revisit only if the
  evaluator needs to be independently visible in the execution-graph viz.

---

## Summary of Changes

| File | Change |
|------|--------|
| `analysis/agents/critique.py` | New: `critique_findings` + `FindingsVerdict` |
| `analysis/agents/base_agent.py` | Finalize gate in `_react_loop`: critic call, feedback injection, exhaustion fallback, calibrated confidence |
| `src/models/results.py` | `EvidenceBundle.verification_note: str \| None = None` |
| `analysis/nodes/analysis_conductor.py` | `_format_bundles` surfaces note; one prompt line on flagged bundles |
| `tests/unit/`, `tests/subgraphs/` | Coverage per Testing section |
