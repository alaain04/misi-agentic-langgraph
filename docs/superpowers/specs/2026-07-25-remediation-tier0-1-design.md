# Remediation v1 — Tier 0/1 Verified Dependency Bumps

**Date:** 2026-07-25
**Status:** approved (design), pending spec review
**Roadmap:** `docs/superpowers/roadmap.md` Workstream C ("Remediation — the tier
ladder"), chunks **C0** (remediation subgraph + verification spine) and **C1**
(Tier 0/1 verified bumps). This is the first rung; C2 (codemods) and C3
(package replacement) are separate later specs that extend, not rewrite, what
this builds.

## Problem

The pipeline today stops at recommendations. It investigates
(discovery → analysis → report) and produces a report where every finding
carries a natural-language recommendation, but it never *acts*: zero repo-write
capability, no verified change, no PR. The north star
(`roadmap.md`) is to turn the loop into
**investigate → recommend → remediate → verify** — open a PR that makes the
change and *proves* the finding is gone and the project still builds before a
human looks.

This spec builds the reliable first rung: **verified dependency bumps**. Given
the analysis findings, produce a set of same-package version bumps that
verifies *together* in the sandbox (install → build → test, plus a
deterministic "is the flagged issue gone?" check), then open a single PR via
the `gh` CLI. Nothing that fails verification is ever proposed.

## Scope decision

**Tier 0/1 only — same-package version bumps.**

- **Tier 0:** bump a direct dependency's range, regenerate the lockfile, verify.
- **Tier 1:** transitive-fix-via-direct-bump — bump the *direct* parent so a
  vulnerable transitive resolves out, using the `is_direct`/`direct_dependents`
  attribution the pipeline already computes.

**Explicitly out of scope for v1** (recorded as `skipped` breadcrumbs, become
later specs):

- **Tier 2** (`bump_with_codemod`): when a bump requires a breaking major and
  the API changes, rewriting call sites. Separate spec — drops into the
  orchestrator's fix-on-failure loop as an additional worker.
- **Tier 3** (`replace`): swapping dependency X for a different package Y.
  Separate spec, research-framed ("do not promise it works").

**Non-goals:**

- No changes to the report subgraph in this spec. How remediation outcomes are
  *surfaced* is deferred until the remediation stage's outcomes are defined and
  built (this spec defines them). Report stays as-is for now.
- No hand-built `create_pull` port/adapter (D2). PR creation uses the `gh` CLI
  under the operator's ambient `gh auth` — pragmatic and thesis-appropriate.
- No new severity config. Reuse `settings.risk_min_severity`.

## Where it sits

```
discovery → analysis → remediation → report        (report untouched this spec)
```

Remediation is a new phase after analysis and before report. It consumes
analysis findings and `prep` directly; it does **not** depend on the report
subgraph's enrichment. Everything it needs — audit fix paths, current version
ranges, directness — it gathers on demand from data already produced or cheaply
re-derivable (the A3 input cache already holds `npm audit` keyed by commit SHA).

## Architecture

### 1. Target selection (deterministic, pre-LLM)

Before any LLM runs, reduce findings to a deduped list of remediation targets:

1. **Filter by severity.** Keep findings at/above `settings.risk_min_severity`
   (the same floor report intake uses — no new config).
2. **Anchor transitives to their direct dependent.** A transitive finding is
   attributed to the direct dep that pulls it in (`is_direct=false` →
   `direct_dependents`). The lever the user controls is always a direct dep, so
   the *bump* is always on a direct dep. (This is the direct-anchoring work
   already shipped in PR #20.)
3. **Unify by direct dep.** Multiple findings whose fix is the *same* direct-dep
   bump collapse into one target — e.g. two different vulnerable transitives
   both pulled in by (or fixed by bumping) the same direct parent. Their
   recommendations merge; the chosen target version must cover **all** issues
   grouped under that dep. Output: one target per direct dep, each recording the
   set of findings it `addresses`.

### 2. The `Remediation` entity — spans Tiers 0/1/2/3

Designed once so later tiers add *behavior*, not schema. v1 only ever writes
`strategy="bump"` and the range fields; the Tier 2/3 fields stay dormant.

```python
class VerificationResult(BaseModel):
    installed: bool = False
    built: bool | None = None            # None = repo has no build script
    tested: bool | None = None           # None = repo has no test script
    finding_resolved: bool | None = None # deterministic where checkable (vuln re-audit)
    logs_snippet: str = ""

class CodeChange(BaseModel):             # Tier 2/3 slot — empty in v1
    file: str
    rationale: str

class Remediation(BaseModel):
    id: str
    addresses: list[str]                 # analysis finding dep_names this covers
    target_dep: str                      # the DIRECT dep acted on (the anchor)
    strategy: Literal["bump", "bump_with_codemod", "replace"] = "bump"
    from_range: str | None = None
    to_range: str | None = None          # bump / bump_with_codemod
    replacement_dep: str | None = None   # Tier 3
    replacement_range: str | None = None # Tier 3
    migration_plan: str = ""             # Tier 2/3 LLM plan; empty for a plain bump
    code_changes: list[CodeChange] = []  # Tier 2/3; empty in v1
    status: Literal["fixed", "failed", "skipped"] = "skipped"
    skip_reason: str | None = None       # "needs major (Tier 2)" | "different package (Tier 3)" | "no fix"
    verification: VerificationResult = Field(default_factory=VerificationResult)
    attempts: int = 0
    patch: str = ""                      # unified diff this remediation contributes
```

Stage output:

```python
class RemediationResult(BaseModel):
    id: str
    job_id: str
    remediations: list[Remediation]
    branch: str | None = None
    pr_url: str | None = None
    consent: bool = False                # was write authorized for this job (D3-lite)
```

### 3. The orchestrator loop (self-correcting, jointly-verified)

A single smart orchestrator (mirroring `analysis_conductor`'s
observe → act → feedback → iterate shape, and reusing the
`_react_loop`/`_feedback_result` self-correction pattern in `base_agent.py`)
drives a **single host-side working copy** — the clone already at
`prep.repo_path`, already mounted into the sandbox container.

```
working copy = host clone at prep.repo_path; applied set starts empty
orchestrator loop (bounded iterations):
  observe: current working copy, applied bumps, last verification feedback
  act:     propose/adjust a to_range for a target (grounded in npm_audit
           fixAvailable + npm_outdated + the merged recommendation),
           or revisit an already-applied bump, or drop a target
  apply:   edit package.json → regenerate lockfile (in the sandbox)
  verify:  install → build (if script) → test (if script) → findings resolved?
           — over the WHOLE working copy, so cross-bump interactions surface here
  → not green: feed the failure back into the loop, re-plan, iterate
  → green AND every target resolved-or-dropped: finalize
finalize → one PR via gh from the jointly-verified state
```

**The invariant:** whatever lands in the PR is a set of bumps that verifies
*together*. If bump B regresses bump A, that is feedback — the orchestrator
re-plans (adjust B, adjust A, try a compatible pair, or drop the unreconcilable
one to `skipped` with a reason) and re-verifies. It never ships an incoherent
set, and it never blindly reverts-and-continues.

**LLM proposes, verification gates.** The LLM picks the target version, but a
wrong pick simply fails the sandbox gate and never becomes a PR. The LLM's
freedom is bounded by verification, not by forbidding it — and it is *grounded*:
`npm_audit`'s `fixAvailable` (`{name, version, isSemVerMajor}`) and
`npm_outdated` are fed in as evidence so it decides from real registry data, not
prose alone.

**Tier boundary enforcement.** If resolving a target would require a breaking
major bump (`isSemVerMajor: true`, or verification fails purely because the
API changed) the orchestrator does **not** attempt code changes — it records
`strategy="bump_with_codemod"`, `status="skipped"`,
`skip_reason="needs major (Tier 2)"`. A recommendation to switch packages →
`strategy="replace"`, `status="skipped"`, `skip_reason="different package
(Tier 3)"`. These breadcrumbs are how the later specs pick up exactly where v1
stopped.

### 4. Verification worker

Runs against the sandbox container (`ContainerRunPort` / `DockerContainerAdapter`,
volume-mounting `prep.repo_path`), package-manager-aware
(`prep.detected_package_manager`):

- **install / lockfile regen:** the package manager's install that regenerates
  the lockfile from the edited `package.json`.
- **build:** run the `build` script if `package.json` declares one; else
  `built=None` (not run, not failed).
- **test:** run the `test` script if declared and non-placeholder; else
  `tested=None`.
- **finding_resolved:** deterministic where checkable — for vulnerability
  findings, re-run `npm_audit` and confirm the flagged advisory/version is gone
  from the resolved tree (no LLM judgment). For categories where "resolved" is
  not deterministically checkable, `finding_resolved=None`, reported honestly.

`VerificationResult`'s nullable `built`/`tested`/`finding_resolved` exist
precisely so a repo with no build or no tests yields **partial** verification we
report truthfully ("installed + vuln gone, but no tests to prove behavior")
rather than a fake green check.

### 5. PR creation (gh CLI, consent-gated)

After the loop finalizes with a jointly-verified state:

- **Consent (D3-lite):** a per-job opt-in flag `remediate: bool` on the analyze
  request. No flag → the whole stage still runs and produces `Remediation`
  records and patches, but **no branch is pushed and no PR is opened**
  (`pr_url=None`, `consent=False`). Writing to a repo never happens silently.
- With consent: host-side `git` creates a branch off the analyzed ref, commits
  the jointly-verified working-copy changes, and `gh pr create` opens **one PR**
  for the job. The PR body lists each `Remediation` (dep, from→to, findings
  addressed, verification result). `gh` runs under the operator's ambient
  `gh auth`, which must have push access to the target repo — true for the
  project's own `misi-e2e-validation-*` fixtures, which are the first live
  target.

## Error handling

- **No eligible targets** (nothing at/above severity floor, or no fixable
  finding) → empty `remediations`, no PR, stage succeeds.
- **A target cannot be reconciled** into the jointly-verified set → dropped to
  `status="failed"` (or `skipped` with a Tier 2/3 reason); the rest still ship.
- **Verification tooling itself errors** (install crashes, container failure) →
  that target's `Remediation` records the failure in `logs_snippet`,
  `status="failed"`; the stage does not abort the job.
- **`gh`/`git` push failure** (e.g. no write access) → `pr_url=None`, the
  patches are still recorded on each `Remediation`; reported honestly, job not
  failed.
- The loop is **bounded** (max iterations) — on exhaustion, whatever subset is
  currently green ships; unresolved targets are `failed`.

## Testing

- **Target selection** (pure, deterministic): severity filtering by
  `settings.risk_min_severity`; transitive → direct anchoring; unification of
  multiple findings onto one direct-dep target with a merged `addresses` list.
  Synthetic findings + dependency graphs, no container.
- **`Remediation`/`RemediationResult` models:** defaults (v1 writes only `bump`
  fields; Tier 2/3 fields dormant), serialization round-trip.
- **Orchestrator loop** with a mocked verification worker: proposes a bump for a
  target; a worker "pass" finalizes; a worker "fail" feeds back and re-plans; a
  cross-bump regression (worker reports the whole-copy verification red after B)
  drives a re-plan rather than a blind revert; bounded-iteration exhaustion
  ships the green subset and marks the rest `failed`. Assert the LLM is fed
  `fixAvailable`/`outdated` evidence.
- **Tier boundary:** a target needing a breaking major → `skipped`
  `"needs major (Tier 2)"`; a package-swap recommendation → `skipped`
  `"different package (Tier 3)"`. No code changes attempted.
- **Verification worker** with a mocked `ContainerRunPort`: build/test scripts
  present → `built`/`tested` populated; absent → `None`; vuln re-audit drives
  `finding_resolved`.
- **Consent gate:** `remediate=false` → stage runs, `pr_url=None`,
  `consent=False`, no `gh`/`git` invoked; `remediate=true` → branch+PR path
  invoked (mock the `gh`/`git` calls; assert one PR, body lists remediations).
- **Known gap (mirrors D1):** unit tests mock the container and `gh`, so they
  prove the Python-side orchestration, target selection, and PR assembly are
  correct, but cannot prove a real install/build/test or a real `gh pr create`.
  First live validation is a manual run against a `misi-e2e-validation-*`
  fixture (e.g. `-cve-direct`, whose `lodash@4.17.11` has a non-major fix) once
  this merges — treat the PR as unverified end-to-end until that run succeeds.

## Deliverables

1. `Remediation`, `VerificationResult`, `CodeChange`, `RemediationResult`
   models (`src/models/`).
2. Target selection (deterministic filter + direct-anchor + unify).
3. The remediation orchestrator (LLM loop, self-correcting, jointly-verified),
   grounded in `npm_audit`/`npm_outdated`.
4. The verification worker (container-backed install/build/test + vuln re-audit).
5. `remediate: bool` request flag (consent) + gh-CLI branch/commit/PR assembly.
6. Wiring: new remediation phase between analysis and report in the main graph.
7. Unit tests per the Testing section.

## Deferred (explicit, not forgotten)

- **Report integration** — how outcomes surface (report stays untouched here).
- **Tier 2 codemods** (`bump_with_codemod`) — the fix-on-failure worker that
  rewrites call sites when a bump breaks the API.
- **Tier 3 replacement** (`replace`) — cross-package migration, research-framed.
- **D2 proper write port** — v1 uses `gh` CLI; a scoped `create_pull` adapter
  can replace it later without touching the orchestrator.
