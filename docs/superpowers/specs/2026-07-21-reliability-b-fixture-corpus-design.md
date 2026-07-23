# Reliability B — E2E Fixture Corpus Design

**Date:** 2026-07-21
**Status:** approved (design), pending spec review
**Roadmap:** `docs/superpowers/roadmap.md` Workstream B. Builds on the reliability
core (A1 dedup, A3 input caching, A4 determinism metric — all merged) and the
direct-anchored-findings feature.

## Problem

The analysis pipeline is validated two ways today: `uv run pytest` (unit
correctness) and `apps/backend/docs/e2e-test-catalog.md` driven by
`scripts/e2e_check.py` (live runs against real repos). The catalog check
asserts only *generic* criteria — an `overall_risk_level` is present,
`recommendations` exist when risk is high, severity and `risk_score` are
mutually consistent. It cannot tell **"correctly clean"** from **"silently
broken."**

This gap is not hypothetical. Catalog Group 1 ran `chalk` and got **zero**
license and supply-chain findings. Is chalk genuinely clean on those axes, or
did those agents silently no-op? With a real third-party repo there is no way
to know — there is no ground truth.

A fixture corpus fixes this: purpose-built repos with **known, pinned issues**
and **golden expectations**, so every detection category has a *positive
control* that must trip, and a *negative control* that must stay silent.

## Non-goals

- Not replacing unit tests or the existing catalog — this is a third layer
  (ground-truth E2E) that extends the catalog's Group 1.
- Not exhaustive ecosystem coverage. Package-manager variants (pnpm, yarn,
  no-lockfile) and further edge cases are an explicit follow-on (see
  "Deferred").
- Not asserting LLM-generated prose, exact `risk_score` values, or exact total
  finding counts — those drift and would make the corpus flaky.

## Architecture

Two coupled halves, because "real GitHub repos" must remain **reproducible** —
a thesis cannot depend on repos whose contents live only on GitHub.

### 1. Fixture sources (source of truth, committed in this repo)

Each fixture is a tiny project committed under
`apps/backend/tests/e2e/fixtures/<name>/`:

- `package.json` — declares the dependency that carries the known issue.
- a **committed lockfile** (`package-lock.json`) — pins the exact resolved
  versions so the known issue is deterministic and does not drift with the
  registry. (This also means the dependency-graph input cache from A3 applies —
  `lock_committed=true`.)
- `README.md` — one paragraph: what issue this fixture encodes and which
  assertion it backs.

A provisioning script `apps/backend/scripts/provision_fixtures.py` materializes
each source directory as a GitHub repo via `gh repo create <name> --public|--private`
plus an initial commit + push. It is **idempotent**: re-running updates existing
repos rather than failing. GitHub is a *mirror*; the committed sources are
authoritative.

All repos are prefixed **`misi-e2e-validation-*`**.

### 2. Manifest + runner (the assertion harness)

- **Manifest** `apps/backend/tests/e2e/corpus.yaml` — a declarative list. One
  entry per fixture: `name`, `repo_url`, `concern`, `visibility`
  (`public` | `private`), and its golden `expectations`.
- **Runner** `apps/backend/scripts/corpus_check.py` — iterates the manifest,
  submits each fixture to the live backend, polls to completion (reusing
  `e2e_check.py`'s submit/poll plumbing — refactor the shared HTTP helpers into
  an importable module rather than duplicating), applies the per-fixture
  assertions, prints a **pass/fail matrix**, and exits non-zero on any failure.
- `private` fixtures are **SKIPPED** with an explicit
  `SKIP (requires PAT — Workstream D1)` line until PAT/private-repo support
  lands. They are pre-positioned acceptance targets for D1, not failures today.

## The fixtures

Ten repos: eight public positive/negative controls (v1's asserted matrix) plus
two private fixtures pre-positioning D1.

| Repo (`misi-e2e-validation-*`) | Visibility | Encodes | Core assertion |
|---|---|---|---|
| `-cve-direct` | public | a direct dep pinned to a stable-advisory vulnerable version | vuln finding for that dep, `is_direct=true`, severity ≥ floor |
| `-cve-transitive` | public | a healthy direct dep whose pinned old version pulls a vulnerable transitive | finding `is_direct=false`, `direct_dependents` contains the direct anchor |
| `-gpl-license` | public | a GPL-3.0 (or AGPL-3.0) dependency | finding present with **license** category (copyleft rule C3) |
| `-unmaintained` | public | a dep with no releases in years | finding present with **maintenance** category |
| `-supply-chain` | public | a dep tripping a supply_chain_agent heuristic (typosquat / postinstall) | finding present with **supply-chain** category |
| `-clean` | public | modern, healthy, permissively-licensed deps | **exactly zero** findings (negative control) |
| `-transitive-fanout` | public | one vulnerable transitive pulled by ≥2 direct deps | `direct_dependents` has ≥2 entries |
| `-false-positive-trap` | public | a dep that *looks* risky by name but is fine | that dep is **not** flagged |
| `-private-cve` | private | same content as `-cve-direct` | (skipped until D1; then = cve-direct assertion) |
| `-private-clean` | private | same content as `-clean` | (skipped until D1; then = exactly zero) |

### Detection mechanisms (grounded, verified against current agents)

- **Vulnerability** → `npm audit` via `vulnerability_agent`. Fixture pins an old
  version with a long-lived advisory. Assertion is **superset** (see below).
- **License** → `license_agent`. `GPL-2.0-*`/`GPL-3.0-*` map to `strong_copyleft`,
  `AGPL-3.0-*` to `network_copyleft` (`license_data.py`); both fire copyleft
  contagion rule C3 (`license_rules.py`). Fixture uses a GPL/AGPL dependency.
- **Maintenance** → `maintenance_agent` staleness. Fixture uses a dep with no
  releases in years. (Maintenance is direct-only per the direct-anchored rule,
  so the stale dep must be a **direct** dependency.)
- **Supply-chain** → `supply_chain_agent` → `typosquat_detection` flags any dep
  whose name is within edit distance ≤2 of a popular package
  (`external_api.py`), plus postinstall-script / metadata heuristics.

### Open research item: the supply-chain fixture

`install_deps` runs `npm install`, so **every declared dependency must actually
resolve and install** — the fixture cannot use a genuinely malicious or
non-existent package. The typosquat check keys on *name*, not behavior, so the
candidate approach is a **real, safe, published** package whose name happens to
be edit-distance ≤2 from a popular one. Planning must resolve exactly which
package, verified installable and harmless.

**Documented fallback:** if no safe deterministic trip exists, the
`-supply-chain` fixture is **downgraded** from an asserted positive control to a
manifest entry marked `assert: manual` (runner reports it, does not gate on it),
and this limitation is recorded in the catalog. The other seven public fixtures
still gate. We do not invent a fake finding to make a green matrix.

## Assertion model

Robust structural assertions only — the corpus runs an LLM pipeline over a
drifting advisory database, so it asserts facts that *must* hold regardless of
model wording or DB growth:

- **Superset for vulnerability fixtures.** The expected dep must appear at or
  above a declared severity floor. Finding *additional* vulns is allowed
  (advisory DBs grow over time) — never assert an exact count or set equality.
- **Category correctness.** gpl/unmaintained/supply-chain each assert the
  expected finding carries the correct category. This is precisely what failed
  silently on chalk.
- **Directness facts.** `is_direct` and `direct_dependents` per fixture
  (cve-direct → `is_direct=true`; cve-transitive → `is_direct=false` with the
  anchor listed; transitive-fanout → ≥2 anchors).
- **Exactly zero** for `-clean`; **not-flagged** for `-false-positive-trap`
  (the named dep must not appear in findings).
- **Never** assert on generated prose, exact `risk_score`, or exact total
  finding count.

Expectation schema (per manifest entry), sketch:

```yaml
- name: misi-e2e-validation-cve-transitive
  repo_url: https://github.com/<owner>/misi-e2e-validation-cve-transitive
  concern: "security vulnerabilities"
  visibility: public
  expectations:
    mode: superset            # superset | exactly_zero | not_flagged | manual
    require_findings:
      - dep_name: <transitive-dep>
        category: vulnerability
        min_severity: high
        is_direct: false
        direct_dependents_contains: <direct-anchor>
```

## Error handling

- **Backend unreachable** → runner exits 2 (matches `e2e_check.py`), no partial
  matrix claimed as pass.
- **A fixture job reaches `failed`/`cancelled`** → that row is a failure with the
  terminal status shown; the runner continues the remaining fixtures and still
  exits non-zero at the end (one broken fixture must not mask the rest).
- **`gh` not authenticated / repo already exists** → provisioning script reports
  clearly and is idempotent (update, don't crash).
- **Private fixture encountered before D1** → SKIP row, not a failure.

## Testing

- Unit tests for the **assertion evaluator** (pure function:
  `expectations × report → list[failures]`) — this is the logic that must be
  correct, and it is testable without a live backend using synthetic report
  dicts. Cover: superset pass/fail, exactly_zero pass/fail, not_flagged,
  category mismatch, directness mismatch, missing expected dep.
- Unit test for **manifest loading/validation** (well-formed vs malformed
  entries).
- The provisioning script and the live end-to-end matrix are exercised manually
  (they need `gh` auth + a running backend + Docker) and documented in the
  catalog, not run in CI.

## Deliverables

1. `apps/backend/tests/e2e/fixtures/<name>/` — 10 fixture source trees.
2. `apps/backend/tests/e2e/corpus.yaml` — manifest with golden expectations.
3. `apps/backend/scripts/provision_fixtures.py` — idempotent GitHub provisioner.
4. `apps/backend/scripts/corpus_check.py` — assertion runner (+ shared HTTP
   plumbing extracted from `e2e_check.py`).
5. Assertion evaluator + manifest loader as importable, unit-tested modules.
6. Catalog update: new section in `apps/backend/docs/e2e-test-catalog.md`
   documenting the corpus, how to provision and run it, and the v1 results.

## Deferred (documented follow-on)

- Package-manager variants: re-express `cve-direct` under pnpm, yarn, and
  no-lockfile.
- Additional edge cases beyond fanout and the false-positive trap.
- Wiring `corpus_check.py` into CI once a hermetic/registry-stable strategy for
  the live backend + Docker exists.
- Flipping the two private fixtures to asserted once Workstream D1 (PAT) lands.
