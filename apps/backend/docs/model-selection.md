# Model selection strategy

How this project decides which LLM backs each agent, and how those decisions
are justified with evidence rather than intuition.

## 1. Current state

Every LLM call in the backend goes through one factory:

```python
# src/utils/llm.py:32
def get_llm(model: Model = Model.GPT_4O_MINI, *, rate_limiter=None, max_retries=None) -> BaseChatModel
```

There are 14 call sites. As of Phase 0
(`.superpowers/sdd/2026-08-09-model-selection-phase0/`), each call site
resolves its model through `AgentRole` -> `src/utils/model_registry.py`'s
`get_role_llm(role)` (two exceptions build the plain model via
`get_llm(resolve_model(role), ...)` and tag the compiled graph afterward,
because `deepagents.create_deep_agent(model=...)` rejects a
`.with_config()`-wrapped model — see inline comments in
`analysis/deepagent/nodes.py` and
`remediation/deepagent/subagent_wrapper.py`). All 14 roles currently
resolve to `Model.GPT_5_4_MINI` by default; a role can be pointed at a
different model via `settings.model_overrides` (env var `MODEL_OVERRIDES`,
a JSON-encoded `{role: model}` dict — pydantic-settings parses dict-typed
fields from a JSON env var automatically), with no source edit required.

| Subgraph | Call site | Role | Shape |
|---|---|---|---|
| analysis | `nodes/understand_concern.py:22` | Parse user concern into `ConcernDraft` | structured |
| analysis | `deepagent/nodes.py:84` | Root deep agent (orchestration) | tool-calling |
| analysis | `deepagent/subagent_wrapper.py:33` | `AgentDispatch` routing | structured |
| analysis | `deepagent/coverage.py:50` | Coverage gate judge | structured |
| analysis | `agents/base_agent.py:34` | Specialist analysis agents | structured |
| analysis | `agents/critique.py:12` | `FindingsVerdict` judge | structured |
| report | `nodes/report_synthesizer.py:15` | Narrative synthesis | prose |
| report | `agents/finding_enricher_agent.py:33` | Finding enrichment | structured |
| report | `agents/impact_analysis_agent.py:26` | Business impact | structured |
| report | `agents/critique.py:12` | `FindingVerdict` judge | structured |
| remediation | `classify.py:27` | r1/r2/r3 tier classification | structured |
| remediation | `investigate.py:27` | Investigation | structured |
| remediation | `plan.py:20` | Fix planning | structured |
| remediation | `deepagent/subagent_wrapper.py:80` | Code-editing deep agent | tool-calling |

Three facts held before Phase 0 landed (see section 6.1-6.3 for what
changed and when):

1. **There is no differentiation to justify yet.** Still true — all 14 roles
   resolve to the same default model. The honest current claim is "one
   default model, uniformly applied by policy, swappable per role without a
   source edit" — a defensible baseline, stated as such.
2. ~~**Model choice is bound at import time.**~~ **Resolved (6.1).** Call
   sites now ask `get_role_llm(AgentRole.X)` for a role, and resolution
   happens per call via `settings.model_overrides`, not at module import. A
   comparison experiment is now a settings/env change, not a source edit.
3. ~~**Cost is measured, but not attributed.**~~ **Resolved (6.2/6.3).**
   `CostCallback` (`src/utils/cost.py:17`) now keys its accumulators on
   `(role, model)` — read off the `agent_role:<role>` tag `get_role_llm`
   attaches to each call — and persists the full per-role breakdown (cost,
   prompt/completion tokens, call count, latency) on `Job.cost_breakdown`,
   exposed via `AnalysisStatusResponse.cost_breakdown`.

## 2. Methodological position

The central claim this strategy has to support is of the form:

> Agent A uses model M rather than M' because, for A's workload, M achieves
> comparable task quality at X% of the cost and Y% of the latency.

Public benchmarks **cannot** establish that claim. They measure a model in
isolation on a fixed harness; what actually runs here is model + prompt + tool
set + retry policy + schema constraint. A model that ranks higher on SWE-bench
may still lose in `remediation/deepagent` because of how our tools are shaped.

So the two evidence layers have distinct, non-interchangeable jobs:

- **Public benchmarks — external validity and candidate shortlisting.** They
  narrow a field of dozens of models to 2-4 plausible candidates per workload
  class, and they let the thesis situate its choices against results the reader
  can independently verify. They are *screening* evidence.
- **Internal corpus evaluation — the causal claim.** Running the real pipeline
  against ground truth is the only thing that licenses a per-agent decision.
  This is *confirmatory* evidence.

Presenting benchmark rankings as the justification for an agent's model would
be the main methodological weakness a reviewer would attack. Presenting them as
the shortlisting step, with corpus results as the decision, is defensible.

### Scope discipline

Do not tune 14 models. With uniform cost and latency profiles unknown, the
first move is to **measure where the budget actually goes**, then differentiate
only the agents that dominate cost, latency, or error rate. The expected shape
is Pareto: two or three agents (the deep agents, and whichever specialist runs
most often) account for most of the spend.

The defensible structure is therefore:

- One **default model**, justified once, applied everywhere by policy.
- A small number of **documented deviations**, each with its own evidence.

That is a stronger thesis result than fourteen weakly-supported choices.

## 3. Workload taxonomy

Agents are grouped by what the model is actually being asked to do. Selection
happens per class, not per agent, unless measurement shows an agent is an
outlier within its class.

| Class | Agents | Dominant requirement | Failure mode |
|---|---|---|---|
| **A. Extraction / classification** | `understand_concern`, `classify`, `subagent_wrapper` (dispatch) | Schema adherence, low latency, high call volume | Wrong enum, malformed schema, over-thinking a trivial input |
| **B. Judging** | `coverage`, `analysis/critique`, `report/critique` | Calibration; agreement with human labels | Sycophancy, rubber-stamping, drift toward accept |
| **C. Evidence-grounded analysis** | `base_agent`, `finding_enricher`, `impact_analysis` | Long-context fidelity, non-hallucination | Fabricated CVEs, versions, or dependents |
| **D. Prose synthesis** | `report_synthesizer` | Fluency, faithfulness to inputs | Confident overstatement; user-facing, so most visible |
| **E. Agentic tool use** | `analysis/deepagent`, `remediation/deepagent` | Multi-turn tool reliability, long-horizon planning, code edits | Tool-call loops, invalid patches, runaway token spend |

Class E is where model capability differences are largest and where a cheap
model is most likely to be false economy — a failed remediation costs a whole
run, not one call. Classes A and B are where a cheaper model is most likely to
be sufficient, because the output space is narrow and schema-constrained.

## 4. Benchmark mapping

For each class, the benchmarks that carry signal — and what each does not tell
you. Record the exact benchmark version and evaluation date; leaderboards move,
and a thesis needs a citable snapshot.

| Class | Primary benchmarks | Signal | Limitation |
|---|---|---|---|
| A | BFCL (function calling), IFEval | Schema/instruction adherence | Saturated at the top; near-ties are noise |
| B | GPQA, MMLU-Pro as weak proxies | General reasoning depth | No public benchmark measures judge calibration on *your* rubric — class B must be settled internally |
| C | RULER / long-context suites, SimpleQA, hallucination evals | Retrieval fidelity over long inputs, factual abstention | Synthetic needle tasks are much easier than reasoning over a real dependency tree |
| D | LMArena preference scores | Human-preferred writing | Preference, not faithfulness; weakest evidence of the five |
| E | SWE-bench Verified, Terminal-Bench, τ-bench, BFCL v3 multi-turn | Long-horizon agentic competence, real code edits | Harness-dependent; published scores use each vendor's own scaffold, not ours |

Two recurring traps to state explicitly in the write-up:

- **Contamination and self-reporting.** Vendor-published scores are run under
  vendor-chosen conditions. Prefer third-party or independently reproduced
  numbers; note when only vendor numbers exist.
- **Aggregate rank hides per-task variance.** A 2-point delta on a leaderboard
  is rarely meaningful for a specific workload.

## 5. Metrics

Four axes, measured per agent role, per candidate model.

**Quality** — the only axis that requires ground truth.
- Corpus assertion pass rate (`src/testing/corpus_assertions.py`: `superset`,
  `exactly_zero`, `not_flagged`).
- Precision / recall on findings. Recall matters most for security concerns;
  precision matters most for user trust — `exactly_zero` fixtures
  (`misi-e2e-validation-clean`) catch false positives directly.
- Schema-failure rate: how often `with_structured_output` retries or fails.
- For class E: patch validity — does the branch build, do tests pass, is the PR
  well-formed.

**Cost** — per run, per agent:
```
cost(agent) = calls_per_run x (avg_in_tok x in_rate + avg_out_tok x out_rate)
```
Rates are already tabulated in `src/utils/cost.py:7`. Note that `_FALLBACK_RATE`
silently mis-prices any model not in the table — adding a candidate model
without adding its rate produces plausible-looking but wrong cost figures. Make
the fallback loud before running any sweep.

**Latency** — wall-clock per node. `duration_ms` exists on conductor artifacts
for three agents only (`base_agent.py:161`, `finding_enricher_agent.py:174`,
`impact_analysis_agent.py:161`); the rest are uninstrumented. For a
user-facing pipeline, p95 end-to-end matters more than mean per-call.

**Reliability** — rate-limit (429) incidence, retry counts, timeout rate. This
has bitten the project before; a model with a lower per-token price but a
tighter rate limit can be more expensive in wall-clock and failure rate.
Determinism is a fifth, thesis-specific concern: `temperature=0` is set in
`get_llm`, but reasoning-style models may not honour it, which weakens
run-to-run reproducibility. `scripts/determinism_check.py` is the existing
instrument for this.

### Decision rule

Per class, weight the axes explicitly and state the weights — an unweighted
"tradeoff" is not a justification. A defensible starting point:

| Class | Quality | Cost | Latency | Reliability |
|---|---|---|---|---|
| A | 0.30 | 0.35 | 0.25 | 0.10 |
| B | 0.55 | 0.25 | 0.10 | 0.10 |
| C | 0.55 | 0.20 | 0.15 | 0.10 |
| D | 0.40 | 0.25 | 0.25 | 0.10 |
| E | 0.60 | 0.15 | 0.10 | 0.15 |

Apply with a floor, not a pure weighted sum: a candidate that fails a quality
gate (e.g. any corpus regression on a security fixture) is rejected regardless
of how cheap it is. Cost optimisation happens only among candidates that clear
the gate.

## 6. Enabling work

In dependency order:

**6.1 Make model choice configurable per role. DONE.** `AgentRole` enum and
a role -> model registry (`src/utils/model_registry.py`) resolved from
`settings.model_overrides`, with env overrides via `MODEL_OVERRIDES`
(JSON-encoded). All 14 call sites ask `get_role_llm(role)` for a role
instead of importing a module-level `get_llm(Model.X)`; an override now
takes effect via settings/env, no source edit. Landed
`.superpowers/sdd/2026-08-09-model-selection-phase0/` (plan tasks 1, 3-5;
commits `b2490d7`, `e1386d2`, `92d11ad`, `90288b3`).

**6.2 Attribute cost and tokens per (role, model). DONE.** `CostCallback`
keys its accumulators on the `agent_role:<role>` tag `get_role_llm` attaches
to each call, and the per-role breakdown (cost, prompt/completion tokens,
call count) is persisted on `Job.cost_breakdown`. Landed same plan (task 2;
commit `a580c17`, regression fix `942b9a0` — nested deep-agent calls
inherit the parent's tag ahead of their own, so attribution must prefer the
*last* matching tag, not the first).

**6.3 Instrument latency uniformly. DONE.** `duration_ms` is now recorded
per role in the same `CostCallback` breakdown rather than only on the three
previously-instrumented conductor artifacts. Landed same plan (task 2;
commit `a580c17`).

**6.4 Unblock the corpus.** `scripts/corpus_check.py` SKIPs all 8 fixtures
because they are private. Auth itself is *not* the blocker: `clone_repo`
has supported authenticated cloning via a `github_token` parameter since
workstream D1 (PR #28, landed 2026-07-23) — see
`src/main_graph/subgraphs/discovery/nodes/clone_repo.py`'s
`_clone_command`. The actual remaining gap is that nobody has run
`scripts/corpus_check.py --assert-live` with `CORPUS_PAT_AVAILABLE=1`
against the real private `misi-e2e-validation-*` fixtures to confirm the
auth path works end-to-end — that live verification step is still
outstanding and is prerequisite work for the evaluation chapter, not
remediation-only work.

**6.5 Build the sweep harness.** Given 6.1-6.4: run the corpus across a model
matrix, emit a per-role table of quality / cost / latency / reliability. Repeat
runs per configuration (n >= 3) so variance is visible; a single run cannot
distinguish a real regression from sampling noise.

## 7. Execution plan

**Phase 0 — Baseline.** Land 6.1-6.3. Run the corpus (once 6.4 lands) with the
current uniform `GPT_5_4_MINI` configuration. Produce: cost per agent, latency
per agent, quality baseline. Deliverable: the Pareto chart that says which two
or three agents are worth differentiating.

**Phase 1 — Shortlist.** For each workload class, pick 2-3 candidates using the
Section 4 benchmark mapping. Record the benchmark snapshot (scores, source,
date). Deliverable: a candidate table with stated rationale for inclusion and
exclusion.

**Phase 2 — Sweep.** Run the matrix on the corpus for the high-impact agents
identified in Phase 0. Hold everything else fixed — prompts, tools, retry
policy — so the model is the only varying factor. Deliverable: the measurement
table.

**Phase 3 — Decide and record.** One decision record per role that deviates
from the default, plus one for the default itself. Deliverable: the
justification artifact the thesis needs.

**Phase 4 — Re-evaluation policy.** Model choices decay. State a trigger: a new
model release in a used family, a pricing change, or a corpus regression
re-opens the relevant decision. Without this the thesis claims are only valid
at a point in time, and saying so explicitly is better than implying permanence.

## 8. Decision record template

One per role, kept next to this document.

```markdown
### Role: <agent role>
Workload class: <A-E>
Selected model: <model>            Date: <YYYY-MM-DD>

Requirements
  - <what this agent must do well, and why>
  - Hard constraints: <schema / context length / tool support / latency budget>

Candidates considered
  | Model | Why shortlisted | Benchmark evidence (source, date) |

Measurement (corpus, n runs)
  | Model | Quality | Cost/run | p95 latency | 429 rate |

Decision
  <chosen model, and the specific tradeoff accepted>

Rejected alternatives
  <model — why not; be specific, "worse" is not a reason>

Re-evaluation trigger
  <what would re-open this decision>
```

## 9. Known risks

- **The corpus is 8 fixtures.** That is enough to catch regressions, not enough
  for statistically strong claims. State the sample size as a limitation rather
  than over-claiming from it.
- **Single provider.** `get_llm` only constructs `ChatOpenAI`. Cross-provider
  comparison requires the deferred-import branches the module's docstring
  already anticipates. If the thesis claims a provider-independent method, the
  implementation should demonstrate at least one non-OpenAI model.
- **Prompt-model coupling.** Prompts were written against `GPT_5_4_MINI`. A
  candidate may underperform because of prompt fit rather than capability. Note
  this when a candidate loses narrowly; it is a confound, not a result.
- **Cost of the evaluation itself.** A full matrix sweep across 8 fixtures,
  several candidates, and n>=3 repeats is itself a significant spend. Budget it,
  and prefer narrowing the matrix over reducing repeats.
