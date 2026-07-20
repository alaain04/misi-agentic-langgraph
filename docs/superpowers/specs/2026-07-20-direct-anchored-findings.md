# Spec: Direct-Dependency-Anchored Findings

**Date:** 2026-07-20
**Scope:** Backend only (`apps/backend`). Frontend badge/labels deferred.

## Problem

The analysis pipeline detects and reports risk findings on direct and
transitive dependencies identically. A finding on a transitive dependency
tells the user to upgrade/replace a package they do not control and cannot
change from `package.json`, so the recommendation is not actionable. Two
distinct issues:

1. **Non-actionable recommendations.** A transitive package can only be
   influenced through the direct dependency that pulls it in. Recommendations
   phrased about the transitive itself ("upgrade `qs`", "replace `qs`") are
   noise.
2. **Inapplicable analyses.** Some analyses are *concrete-harm* checks (the
   risk is a fact about code/obligation physically present in the tree) and
   are real at any depth. Others are *quality-proxy* checks ("old",
   "unmaintained") that only make sense for a dependency the user actually
   chose. A stale transitive dependency under a healthy direct parent is not
   an actionable risk — it is the parent maintainer's concern.

## Principle (general rule)

**Detection happens wherever the issue physically is; every recommendation is
anchored on a direct dependency — the only lever the user controls.** A
transitive issue is presented as "your direct dependency `D` pulls in `T`,
which has problem X; here is what to do about `D`", never as an action on `T`.

## Decisions

- **D1 — Maintenance is direct-only.** The `maintenance_agent` (quality-proxy)
  must never emit a finding for a transitive dependency. Enforced
  deterministically (drop non-direct findings), not by prompt alone.
- **D2 — Concrete-harm findings keep full-tree detection**
  (`vulnerability_agent`, `web_research_agent`, `supply_chain_agent`,
  `license_agent` continue to scan transitive packages) but their
  recommendations are re-anchored on the direct dependent(s).
- **D3 — Keep `dep_name` as the issue's true location** (direct or transitive)
  for identity/dedup and detection honesty. Do NOT re-key findings onto the
  direct dependency. Instead add `is_direct` + `direct_dependents` fields and
  make the enricher write `recommendation`/`business_impact` in terms of the
  direct dependent(s).
- **D4 — Fan-out: one finding lists all direct dependents.** A transitive
  pulled by multiple direct deps produces a single finding whose
  `direct_dependents` lists all of them; the recommendation covers updating
  each, because every dependent must be bumped to clear the transitive.
- **D5 — Use the audit fix path.** `npm audit`'s `fixAvailable` is already
  embedded in the finding description by `audit_parser._fix_note` (e.g. "Fix
  requires `X@Y`"), where `X` is the direct dep to bump. The enricher prompt
  uses it when present. When no fix resolves it, the finding says so honestly
  and pivots to "replace `<direct-dep>` or accept the risk" — never "patch the
  transitive", no `overrides`/`resolutions` pins.
- **D6 — `direct_dependents` is computed offline** from
  `prep.dependency_graph` (already built in discovery), not via a container
  call. No new runtime cost.
- **D7 — Deterministic enforcement over prompt-only.** This repo has a history
  of prompt-only rules silently leaking (the maintainer-count rule). The
  maintenance direct-only filter and the `is_direct`/`direct_dependents`
  fields are set in code; prompt changes are additive on top.

## Categorization reference

| Agent | Class | Transitive behavior |
|-------|-------|---------------------|
| `vulnerability_agent` | concrete-harm | detect at any depth; recommend on direct dependent |
| `web_research_agent` | concrete-harm | detect at any depth; recommend on direct dependent |
| `supply_chain_agent` | concrete-harm | detect at any depth; recommend on direct dependent |
| `license_agent` | concrete-harm | detect at any depth; recommend on direct dependent |
| `maintenance_agent` | quality-proxy | **drop** transitive findings (direct-only) |

## Out of scope

- Frontend changes (badge "transitive via X", labels). Deferred.
- Re-keying/aggregating findings by direct dependency (rejected: breaks
  `dep_name` identity/dedup).
- `overrides`/`resolutions` pin recommendations (rejected earlier: too large a
  change).
- Auto-remediation / PR creation.

## Success criteria

- `is_direct(graph, name)` and `direct_dependents(graph, name)` pure helpers
  exist with unit coverage for direct, transitive, shared-transitive, and
  no-lockfile-fallback cases.
- `maintenance_agent` drops transitive findings deterministically; direct
  findings pass through unchanged.
- `ReportFinding` carries `is_direct: bool` and `direct_dependents: list[str]`,
  set deterministically by the enricher (not the LLM).
- The enricher system prompt instructs an unconditional "recommendation always
  targets a direct dependency" rule, with the transitive branch naming the
  direct dependents and forbidding action on the transitive.
- Full backend suite, ruff, and mypy green.
