# Spec: A4 — Determinism Metric

**Date:** 2026-07-20
**Workstream:** A (Reliability & Determinism) — chunk A4.
**Parent:** `docs/superpowers/roadmap.md`
**Depends on:** A1 (duplication removed first, or the metric measures a known
bug), and Workstream B (fixture corpus) for controlled inputs — but can
bootstrap against `chalk/chalk` before B lands.
**Scope:** Backend + a runnable script; no product-code behavior change.

## Problem

We have no way to *measure* reliability. "It feels non-deterministic" is not a
number. Once A1 removes the duplication bug, we need a metric that answers: given
the same repo + concern + commit, how stable is the finding set across repeated
runs? This number is the reliability gate for building code-generating
remediation (Workstream C) and is a headline result for the thesis.

## What it measures

For a fixed (repo, concern, commit), run the pipeline **N times** and report
finding-set stability across runs:

- **Primary metric — dep-name Jaccard:** mean pairwise Jaccard similarity of the
  set of `dep_name`s across the N runs. Captures "does it find the same
  packages every time."
- **Secondary metric — finding-tuple Jaccard:** same, over
  `(dep_name, severity)` tuples. Captures severity stability, not just presence.
- **Reporting:** min/mean/max finding count, the two Jaccard means, and the
  symmetric difference (which findings appeared in some runs but not others) so
  a human can see *what* is unstable, not just how much.

Deliberately **not** exact-equality — some variance is inherent to the agentic
design; the goal is a bounded, tracked number, not a false promise of zero.

## Design

- A standalone script (extend or sit alongside `scripts/e2e_check.py`), e.g.
  `scripts/determinism_check.py --repo <url> --concern <text> --runs N
  [--commit <sha>]`, that drives N full analyses against a running backend and
  computes the metrics above.
- Reads results via the existing `GET /analyze/{trace_id}` API (same flow the
  e2e catalog uses) — no coupling to internals.
- Emits a compact report (table + the symmetric-difference detail) and a
  machine-readable JSON blob for later trend tracking.
- **Fixture-backed mode (once B exists):** run against each fixture repo, so the
  inputs are pinned and the only variance is the pipeline's own. Until then,
  `chalk/chalk` is the bootstrap target (already known to exercise vuln +
  maintenance + fan-out).

## Interaction with other chunks

- **Gates A2:** run A4 *after* A1. If the dep-name Jaccard is already ~1.0
  post-A1, the remaining LLM variance is negligible and **A2 (temperature/seed
  levers) may not be worth doing** — A4 is what tells us. Set the concrete
  "good enough" threshold once A4 produces baseline numbers (Open question in
  the roadmap).
- **Feeds Workstream C:** the metric is the gate for "is analysis reproducible
  enough to safely generate code changes."
- **Feeds the thesis:** the before/after-A1 numbers and the residual-variance
  numbers are directly reportable reliability results.

## Testing

- Unit-test the metric math (Jaccard, symmetric difference, count stats) as pure
  functions with synthetic run sets — no backend needed.
- The script's live-driving path is validated manually against a running
  backend, like the rest of the e2e catalog.

## Success criteria

- `determinism_check` runs N analyses and reports dep-name Jaccard,
  finding-tuple Jaccard, count stats, and the unstable-finding diff.
- Metric math is unit-tested as pure functions.
- Produces a baseline number for `chalk/chalk` post-A1, and per-fixture numbers
  once B exists.
- Results feed back into `apps/backend/docs/e2e-test-catalog.md` (a new
  determinism column/section) and inform the A2 go/no-go.

## Out of scope

- Reducing variance (that's A2/A3). A4 only *measures*.
- CI wiring of the metric — can follow once a threshold is agreed.
