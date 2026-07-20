# Spec: A2 — LLM-Step Determinism Levers

**Date:** 2026-07-20
**Workstream:** A (Reliability & Determinism) — chunk A2.
**Parent:** `docs/superpowers/roadmap.md`
**Depends on:** A1 (duplication removed) and A4 (metric). **Conditional** — only
worth building if A4 shows residual finding-set variance after A1.
**Scope:** Backend only.

## Problem

After A1 removes the duplication bug, the deterministic-source findings (vuln,
license) are stable, but the **LLM-judgment agents** (`maintenance_agent`,
`supply_chain_agent`, `web_research_agent`) and the **conductor** can still
produce different finding sets across runs, because each makes non-deterministic
LLM calls (which tools to call, how many ReAct iterations, whether a finding
passes critique). A2 reduces that residual variance where cheaply possible.

**This spec is conditional:** if A4's dep-name Jaccard is already at/near the
agreed threshold post-A1, A2 is not worth doing. Do not build A2 until A4 has
produced numbers that justify it.

## Design (levers, cheapest first)

1. **Pin sampling parameters.** Set temperature to 0 (and a fixed seed where the
   provider/model supports it) on every structured LLM call — the conductor
   (`analysis_conductor`), the domain agents' `_react_loop` (`base_agent`), the
   critique steps, and the report enricher. This is the highest-leverage,
   lowest-risk lever. Centralize it in the shared LLM factory (`src/utils/llm`)
   so it applies uniformly rather than per-call.
2. **Stabilize structured-output handling.** Ensure retries on structured-output
   parse failure don't silently change behavior run-to-run; make any
   tie-breaking (e.g. ordering of dispatches, ordering of tool results) explicit
   and deterministic.
3. **Deterministic ordering at fan-in.** Where parallel agent results are merged
   (`bundle_ids` accumulation, finding ordering), sort by a stable key so the
   same inputs always produce the same output order — removes ordering noise
   that can cascade into different conductor decisions on the next iteration.

Note the inherent ceiling: multi-agent tool-calling with an LLM will never be
fully deterministic even at temperature 0 (model non-determinism, tool-timing).
The goal is *bounded, measured* variance (tracked by A4), not zero.

## Measurement

Re-run A4's determinism metric before and after each lever; keep only the levers
that move the Jaccard number materially. This keeps A2 evidence-driven rather
than speculative.

## Testing

- Unit-test that the LLM factory applies the pinned parameters (temperature/seed)
  to constructed clients.
- Unit-test any new deterministic ordering/tie-break helpers with synthetic
  inputs.
- Determinism improvement itself is validated via the A4 metric, not unit tests
  (it's a statistical property, not a single assertion).

## Success criteria

- Sampling parameters pinned uniformly via the shared factory, unit-tested.
- Fan-in ordering deterministic, unit-tested.
- A4 metric shows a measured improvement in finding-set Jaccard attributable to
  the levers (or a documented finding that the levers don't help, justifying
  stopping).

## Out of scope

- Caching (A3).
- Any change that would trade correctness for determinism (e.g. suppressing
  legitimately-variable findings just to stabilize the number).
