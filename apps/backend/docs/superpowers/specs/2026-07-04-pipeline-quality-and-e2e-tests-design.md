# Pipeline Quality Improvements & E2E Test Suite

**Date:** 2026-07-04
**Scope:** `apps/backend`

---

## Context

A full E2E run on `jsynowiec/node-typescript-boilerplate` (concern: vulnerable dependencies) surfaced three categories of issues ranked by priority:

| Category | Issue |
|----------|-------|
| A — Output quality | `overall_risk_level` missing from report; `recommendations` array empty |
| B — Scoring accuracy | `severity` label can be MEDIUM/HIGH with < 10% confidence, contradicting the risk_score |
| C — Observability | No intermediate artifacts for `evidence_collector` / `evidence_correlator`; pipeline is opaque during execution |

The goal is to fix A → B → C in order, validated by an expanded 3-run E2E test suite covering distinct concerns and repo profiles. The system handles **Node.js only** (SBOM via Docker + node:XX-alpine).

---

## Fix A — Output quality (`report_builder.py`)

**Changes:**

1. **`overall_risk_level`** — added field computed as the maximum severity across all findings.
   - Values: `"none" | "info" | "low" | "medium" | "high" | "critical"`
   - If no findings: `"none"`
   - Derived from `_SEVERITY_ORDER` rank (same ordering used elsewhere)

2. **`recommendations`** — top-level aggregated list of non-null `finding.recommendation` strings.
   - Ordered by descending `risk_score` of the finding that produced each recommendation
   - De-duplicated (exact string match)
   - Empty list `[]` only when all findings have `recommendation = null`

No LLM calls. No schema changes. Pure deterministic assembly.

---

## Fix B — Severity/confidence accuracy (`confidence.py`)

**Root cause:** `compute_severity` returns the max evidence severity across all supporting evidence, without regard to confidence level. A single `medium`-severity evidence item at 6% confidence produces `severity="medium"` and `risk_score = 5.0 × 0.06 = 0.3` — a contradictory label/score pair.

**Fix:** In `compute_severity`, filter supporting evidence to items where `confidence >= 0.25` before taking the max. If no evidence passes the threshold, return `"low"`.

**Threshold rationale:** 0.25 excludes clearly unreliable signals (< 25% confidence) while preserving genuinely uncertain but real detections. It does not affect `compute_risk_score` — the numeric score already encodes confidence correctly.

**Effect on previous run:**
- `@typescript-eslint/*`: was MEDIUM (score 0.3) → becomes LOW (score 0.3, consistent)
- All other findings unchanged (their confidence was above threshold)

---

## Fix C — Pipeline observability

**`evidence_collector`** (currently a no-op node) gains an artifact write:

```json
{
  "node": "evidence_collector",
  "status": "done",
  "data": {
    "total_evidence": 12,
    "by_dep": { "@babel/parser": 2, "eslint": 3, "vite": 1 },
    "skills_run": ["VulnerabilitySkill", "ReachabilitySkill"]
  }
}
```

**`evidence_correlator`** gains an artifact write after synthesis:

```json
{
  "node": "evidence_correlator",
  "status": "done",
  "data": {
    "findings_count": 4,
    "contradictions_count": 0,
    "deps_covered": ["@rolldown/*", "@typescript-eslint/*", "rolldown", "eslint"]
  }
}
```

`skill_executor` runs as N parallel `Send()` instances — individual instance artifacts would be noisy. The collector summary is the right aggregation point.

---

## E2E Test Suite

Three additional runs (in addition to the baseline run already completed):

### Run 2 — `expressjs/express`
- **Concern:** "Are there vulnerable or outdated production dependencies in this Express.js project?"
- **Expected behavior:** Non-trivial findings (express has older transitive deps). Tests the high-confidence vulnerability path.
- **Success checks:** Plan targets production deps; at least one finding with `risk_score > 2.0` and populated `recommendation`.

### Run 3 — `nicolo-ribaudo/tc39-proposal-iterator-helpers`
- **Concern:** "Are any dependencies using GPL or other restrictive licenses that could affect open-source distribution?"
- **Expected behavior:** Minimal deps, likely license-clean. Tests the "low risk / clean" report path and the LicenseSkill assignment.
- **Success checks:** Plan includes license-themed hypotheses; report is coherent with near-zero risk findings.

### Run 4 — `Rich-Harris/degit`
- **Concern:** "Are any dependencies in this project abandoned or poorly maintained?"
- **Expected behavior:** Some older deps with reduced maintainer activity. Tests the MaintainerTrustSkill path.
- **Success checks:** Plan targets maintainer and ecosystem hypotheses; `MaintainerTrustSkill` assigned in at least one hypothesis.

---

## Success Criteria (all runs)

| Criterion | Check |
|-----------|-------|
| `overall_risk_level` present | `report.overall_risk_level is not None` |
| `recommendations` populated when risk exists | `len(recommendations) > 0` when any `risk_score > 2.0` |
| No severity/score contradiction | No finding where `severity in (medium, high, critical)` and `risk_score < 1.0` |
| Plan concern alignment | Plan hypotheses address the stated concern (not generic) |
| HITL gate 1 fires | Job reaches `awaiting_approval` after discovery |
| HITL gate 2 fires when findings exist | Job reaches `awaiting_approval` again when findings are non-empty |
| Clean completion | Job reaches `done` |

---

## Execution Strategy

Fixes applied in order A → B → C, each validated by:
1. Re-running the baseline concern (vulnerable deps on `jsynowiec/node-typescript-boilerplate`) to confirm no regression
2. Running the three new test cases
3. Checking all success criteria automatically via a poll-and-check script

If a criterion fails, root cause is identified in the logs before the next fix attempt.

---

## Out of scope

- Python, Go, or other ecosystem support
- Frontend changes
- New skills or ingestion subgraphs
- Changes to the HITL gate trigger logic
