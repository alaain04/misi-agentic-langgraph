# Spec: A1 — Eliminate Spurious Finding Duplication

**Date:** 2026-07-20
**Workstream:** A (Reliability & Determinism) — chunk A1, the gating fix.
**Parent:** `docs/superpowers/roadmap.md`
**Scope:** Backend only.

## Problem

Two identical analysis runs against `chalk/chalk` returned 20 findings then 10.
Investigation proved this is **not** LLM variance: run 1's 20 findings were
**exactly** run 2's 10 unique findings, each duplicated 2× (verified against the
stored `analysis_results` document — every one of the 10 `dep_name`s had count
exactly 2, `iteration_count=3`, 4 evidence bundles).

The vulnerability and license findings are deterministic at their source
(`npm audit` and the SPDX rules table don't vary for a fixed lockfile), so their
apparent non-determinism is *entirely* this duplication bug. Fixing it makes
those findings reproducible.

## Root cause (proven)

1. **The conductor re-dispatches the same agents across iterations.**
   `analysis_conductor.py` runs up to `_MAX_ITERATIONS = 4` iterations. Its
   system prompt says "Every dispatch must be unique … Never re-run a
   combination already answered" and "Dispatch [the whole-tree agents] at most
   once" — but this is **prompt-only, not enforced in code**. The LLM
   non-deterministically re-dispatches whole-tree agents (`vulnerability_agent`)
   and package-scoped agents (`maintenance_agent`) in a later iteration.
2. **The sink flattens every bundle with zero dedup.**
   `save_analysis_result.py` does
   `all_findings = [f for b in bundles for f in b.findings]` across all
   accumulated bundles (`bundle_ids` is `Annotated[list[str], operator.add]`, so
   it accumulates across iterations). Whole-tree agents return their full finding
   set on every dispatch, so a re-dispatched agent's findings appear N times.

Net: re-dispatch × no-dedup = exact N× duplication in the analysis result, which
then flows unchanged into the report.

## Design

Two deterministic layers, consistent with this repo's established preference for
**deterministic enforcement over prompt-only rules** (prompt-only discipline has
silently failed to hold here before).

### Layer 1 — Dedup at the sink (correctness guarantee)

In `save_analysis_result.py`, dedup `all_findings` before persisting, keyed on
`(dep_name, severity, description)`:

- Collapses identical duplicates produced by a re-dispatched agent (the two
  copies are byte-identical: same agent, same `npm audit`/rules run, same
  `FindingNote`).
- **Preserves genuinely distinct findings on the same package** — e.g. `electron`
  flagged by both `vulnerability_agent` and `supply_chain_agent` has different
  `description`s, so both survive. Dedup must never merge across distinct issues.
- Order-stable: keep first occurrence, preserve the existing
  most-severe-first ordering from `parse_audit_findings` and
  `filter_by_min_severity`.

This is placed at the **analysis stage** (on `FindingNote`, before enrichment) so
duplication can never propagate into the per-finding report enrichment — which
is where the duplicates previously diverged into different recommendation text.

This layer alone guarantees a duplicate-free report regardless of conductor
behavior.

### Layer 2 — Whole-tree dispatch cap at the source (cost)

In `analysis_conductor.py`, after the LLM produces `decision.dispatches`,
deterministically drop any dispatch for a **whole-tree agent**
(`vulnerability_agent`, `license_agent`) whose `agent_type` already appears in
`state["agent_calls"]` (each `AgentCallRecord` carries `agent_type`). These
agents scan the entire tree in one run — a second dispatch adds zero coverage
and only duplicates work and cost. This is the biggest and cleanest cost saver
and targets exactly the deterministic-source agents.

Layer 2 is a **cost/efficiency optimization**, not a correctness requirement —
Layer 1 already guarantees output correctness. Keeping Layer 2 narrow (whole-tree
agents only, using data already in `state`) keeps the change minimal and low
risk.

## Out of scope (noted, not fixed here)

- **General `(agent_type, hypothesis, packages_to_focus)` tuple dedup** of
  package-scoped agents (e.g. `maintenance_agent` re-dispatched on the same
  packages). Layer 1 already makes its *output* correct; suppressing the wasted
  re-run cleanly would require `agent_type` on `EvidenceBundle` (bundles carry
  `domain`, not `agent_type`). Deferred as a follow-up enhancement.
- **Frontend `dep_name` collision:** the report UI keys findings on `dep_name`
  alone, so two *distinct* findings on one package would collide in the list.
  Pre-existing, separate concern.
- **Genuine LLM-judgment variance** across runs (maintenance/supply-chain/
  web-research producing different finding *sets*): this is Workstream A2/A4, not
  A1.

## Testing

Pure unit tests, no LLM:

- **Layer 1:** construct several `EvidenceBundle`s whose `findings` include exact
  duplicates (same `dep_name`/`severity`/`description`) plus one same-`dep_name`
  finding with a different `description`; assert the saved result contains one
  copy of each duplicate and keeps the distinct one; assert severity ordering is
  preserved. Test the dedup as an isolated pure helper so it needs no DAO/DB.
- **Layer 2:** call `analysis_conductor` (or an extracted pure dispatch-filter
  helper) with an `agent_calls` state already containing `vulnerability_agent`,
  and a decision that re-dispatches it; assert the re-dispatch is dropped and a
  novel dispatch is kept. Assert a package-scoped agent is *not* capped by this
  filter.

Prefer extracting the dedup and the dispatch-filter as **pure functions** so both
are unit-testable without the graph, the DAO, or an LLM.

## Validation (optional, manual)

Run `chalk/chalk` N times via the e2e catalog flow; assert the finding count
equals the unique count with no doubling across runs. The deterministic unit
tests are the real gate; this is a live confirmation.

## Success criteria

- Deterministic dedup helper collapses identical findings, preserves distinct
  ones, is order-stable, and is unit-tested.
- Whole-tree agents (`vulnerability_agent`, `license_agent`) are dispatched at
  most once per job, enforced in code and unit-tested.
- No behavior change when there is nothing to dedup (single-iteration runs are
  byte-identical to today).
- Full backend suite, ruff, mypy green.
