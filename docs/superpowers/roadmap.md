# Roadmap — From Analyst to Verified Remediator

**Status:** living document. Last updated 2026-07-20.
**Purpose:** the strategic direction for the project beyond the
direct-anchored-findings work. Each workstream below is broken into chunks
sized to become their own spec (`docs/superpowers/specs/`) and plan
(`docs/superpowers/plans/`) when picked up. This document is the parent those
specs descend from — it is not itself an implementation plan.

---

## Where we are (2026-07-20)

The pipeline today is an **investigator that explains**: given a repo URL and a
natural-language concern, it plans an agentic investigation
(discovery → analysis → report), grounds findings against evidence with a
critique/self-correction loop, and produces a report where every
recommendation is anchored on a direct dependency (the only lever the user
controls). Shipped: PR #20 (direct-anchored findings + lockfile fallback).
An e2e quality catalog exists (`apps/backend/docs/e2e-test-catalog.md`) with
Group 1 executed.

**Honest assessment carried forward** (see the evaluation discussion of
2026-07-20):

- For the *vulnerability* category, outputs largely re-present `npm audit`
  (including its fix path) — slower and costlier than running `npm audit`
  directly. Real added value is in the non-vuln categories (maintenance,
  license, supply-chain), the unified/explained synthesis, and reachability.
- The research contribution is the **agentic-investigation architecture + the
  grounding/anti-hallucination discipline**, not "detects vulns better than
  Snyk" (it won't).
- Two liabilities gate everything that follows: **non-determinism** (identical
  input gave 20 findings then 10 in two chalk runs) and **it doesn't act**
  (stops at recommendations; zero repo-write access).

## North star

Turn the loop into **investigate → recommend → remediate → verify**: not just
"upgrade xo to 0.60.0", but open a PR that makes the change, adjusts the code
the change requires, and proves the finding is gone and the project still
builds — before asking a human to look. The differentiator over Dependabot is
precisely the parts Dependabot can't do (transitive-fix reasoning, code
migration) *plus* the verification gate.

## Guiding principles

1. **Reliability is a prerequisite, not a parallel track.** A non-deterministic
   *recommendation* is a nuisance; a non-deterministic *code change* is
   dangerous. Workstream A gates Workstream C — we do not generate diffs until
   the analysis is reproducible.
2. **Verification is the spine.** Any generated change is worthless — worse,
   actively dangerous because it looks authoritative — unless it is verified
   (install → build → test) in the sandbox before the PR is opened. The PR
   must surface the verification result honestly.
3. **Scope honesty over demo ambition.** Tiers 2/3 (code migration, package
   replacement) are a research frontier bounded by the target repo's test
   quality. We ship the reliable core (Tiers 0/1) and present 2/3 as
   best-effort, human-review-required, with an honest evaluation of failure
   modes — never as "it always works."
4. **Least-privilege, explicit-consent for write access.** Writing to a user's
   repo is a new trust boundary. Tokens are scoped minimally, never logged or
   persisted in the job store, and every write is per-job opt-in.

---

## Workstreams at a glance

```
A. Reliability & Determinism ──┐ (gates C)
                               ├──► C. Remediation (tier ladder)
B. E2E Fixture Corpus ─────────┘        ▲
   (measures A, validates C)            │
                                        │
D. Repo Access & Credentials ──────────┘ (enables C's write/PR + private repos)
```

Rough sequence: **A + B first** (measure and harden reliability on controlled
inputs) → **D** (credentials, read-private then write) → **C** incrementally
(Tier 0/1 reliable core, then 2/3 as research). B is continuous — the fixture
corpus grows as each capability lands and becomes its regression net.

---

## Workstream A — Reliability & Determinism (gating)

**Why first:** it is the prerequisite for everything in C, and it is the
liability most undermining the project's core claim (that its grounding makes
LLM output trustworthy). A tool that answers differently on identical input
can't be trusted to change code.

**A1 — Root-cause the finding-count non-determinism.**
Diagnose the 20-vs-10 divergence. Leading hypothesis (from catalog notes): the
conductor re-dispatching the same domain agent across iterations without the
dedup its prompt claims, producing duplicate findings for the same `dep_name`.
Deliverable: a documented root cause + a deterministic dedup of findings keyed
by `(dep_name, issue_type)`.

**A2 — Determinism levers on the LLM steps.**
Temperature/seed pinning where the provider supports it; stabilize structured
output parsing; make every post-processing/filter step deterministic (the
codebase already prefers deterministic post-filters over prompt-only rules —
extend that). Accept that multi-agent tool-calling can't be fully
deterministic; the goal is *bounded* variance, not zero.

**A3 — Reproducibility via input caching.**
Cache the deterministic inputs — dependency graph, `npm audit` output,
CodeGraph index — keyed by repo commit SHA, so at minimum the same
commit yields the same evidence base across runs. Reduces cost/latency too.

**A4 — A determinism metric.**
Run each fixture (Workstream B) N times; report finding-set stability (e.g.
Jaccard similarity of the `dep_name` set, and of the full finding tuples)
across runs. This is the headline reliability number for the thesis and the
gate for "is C safe to build yet."

Chunks: A1 (bugfix), A2 (LLM determinism), A3 (caching), A4 (metric) — each a
candidate spec.

---

## Workstream B — E2E Fixture Corpus & Measurement

**Why:** you cannot measure reliability (A4) or validate remediation (C)
without controlled inputs with known-correct expected outputs. Public repos
drift and mostly return zero findings on any single axis (chalk returned 0 for
license and 0 for supply-chain — see catalog Group 1), so they can't exercise
those paths. The fix is a **corpus of purpose-built GitHub repositories with
deliberately planted, version-pinned issues**, each with a golden expected
output.

**B1 — Corpus design & hosting.**
Create fixtures under a dedicated location (e.g. a `misi-e2e-fixtures` GitHub
org, or `misi-fixture-*` repos). Each fixture is small, version-**pinned**
(exact versions, committed lockfiles — no ranges that drift), and targets one
axis:

| Fixture | Plants | Validates |
|---------|--------|-----------|
| `fixture-cve-direct` | a direct dep pinned to a version with a known CVE | direct vuln finding, correct CVE + fix version |
| `fixture-cve-transitive` | direct dep whose subtree pins a known-vulnerable transitive | `is_direct=false`, `direct_dependents=[parent]`, fix path |
| `fixture-transitive-fanout` | a vulnerable transitive reachable via 2+ direct deps | `direct_dependents` lists **all** parents (D4) |
| `fixture-gpl-license` | a GPL/AGPL dep (direct and transitive variants) | license finding + anchored recommendation (closes catalog 1.3 gap) |
| `fixture-unmaintained` | a genuinely abandoned direct dep **and** an abandoned transitive | maintenance finding on the direct one; the transitive one is **dropped** (D1 negative test) |
| `fixture-supply-chain` | a benign postinstall script / suspicious metadata (never real malware) | supply-chain finding (closes catalog 1.4 gap) |
| `fixture-clean` | genuinely no issues | **zero findings** — any finding is a measured false positive |
| `fixture-pnpm` / `fixture-yarn-classic` / `fixture-yarn-berry` | same issue, different package manager, lockfile committed | per-PM parser correctness |
| `fixture-*-nolock` | same, **no committed lockfile** | `install_deps` lockfile-only fallback per PM (npm/pnpm; yarn has none by design) |
| `fixture-no-package-json` / `fixture-zero-deps` / `fixture-monorepo` | edge shapes | graceful degradation, workspace scoping |

**B2 — Golden expected outputs + assertion harness.**
Per fixture, a checked-in expected-output file (findings, `is_direct`,
`direct_dependents`, severities). Harness asserts **superset/contains** rather
than exact equality for vuln fixtures, because `npm audit` results legitimately
grow as new advisories publish — assert the planted issue is present and
correctly shaped, tolerate additional real findings. The `fixture-clean`
false-positive check is the exception: it asserts exactly zero.

**B3 — Wire into the reliability metric (A4) and CI.**
Run the corpus N times for determinism, and once per change as a regression
gate. Extend/replace `scripts/e2e_check.py` (which today only checks structural
criteria, not `is_direct`/anchoring) with the catalog's quality rubric checks.

**B4 — Fold results back into `e2e-test-catalog.md`.**
The catalog's Group 1–6 tables become fixture-backed where possible; the
append-only execution log keeps accumulating real runs.

Chunks: B1 (build corpus repos — needs the write/PAT capability from D, or
manual creation), B2 (golden + harness), B3 (metric + CI), B4 (catalog merge).

**Note on ordering:** B1 can start with manually-created fixture repos
immediately; automating their creation can wait for D. Don't block the corpus
on the credential work.

---

## Workstream C — Remediation (the tier ladder)

The core new capability. Structured as a difficulty ladder because the tiers
have wildly different feasibility, risk, and novelty. **Verification (the
sandbox install→build→test loop) is shared spine across all tiers.**

**C0 — The remediation subgraph + verification spine.**
A new phase after `report`: takes a finding + its recommendation, produces a
candidate change, and **verifies it in the existing container sandbox** before
anything is proposed. Reuses the agent self-correction loop for
fix-on-failure. This chunk builds the spine and the Tier-0 mechanical bump; no
credentials/PR yet (output a diff artifact, not a PR). Depends on: A (reliable
recommendations) partially in place.

**C1 — Tier 0/1: verified dependency bumps (the reliable core).**
- Tier 0: version bump of a direct dep (change range, regenerate lockfile,
  verify build+tests).
- Tier 1: transitive-fix-via-direct-bump — bump the direct parent so the
  vulnerable transitive resolves out, using the `direct_dependents` + audit
  fix path we already compute. **Deterministic success criterion:** regenerate
  the lockfile and confirm the vulnerable version is gone from the resolved
  tree — no LLM judgment in the verification.
- This is the sweet spot: reliable, checkable, genuinely more than Dependabot
  (Tier 1 reasoning), and it closes the loop on the direct-anchoring work.

**C2 — Tier 2: same-package major upgrade with codemods.**
When a bump breaks the API, migrate call sites (CodeGraph/blast-radius already
locates them; an LLM codemod agent rewrites them). **Hard verification gate is
mandatory** — a wrong edit silently breaks the user's code. Reliability bounded
by the target's test suite; be explicit about that. Research-flavored but
engineerable for well-tested targets.

**C3 — Tier 3: cross-package replacement (research frontier).**
Replace dep X with a different package Y (different API, semantics, feature
coverage). Genuinely hard — "does it still do what it did?" is undecidable in
general; the LLM will produce confident-but-wrong migrations. **Do not promise
it works.** Deliverable framing: a verified, self-correcting migration
*methodology*, evaluated honestly (success rate on repos with good tests,
documented failure modes), not a reliability claim.

**C4 — PR assembly & the closed loop.**
Assemble the verified change into a PR (needs D). The closing move — re-run the
analysis on the patched tree and show the finding is gone — is both a strong
demo and a genuine correctness signal. Bundle the version change + code changes
in one PR, exactly as originally envisioned.

Chunks: C0 (subgraph + verify spine), C1 (Tier 0/1), C2 (Tier 2), C3 (Tier 3),
C4 (PR assembly + closed loop). C1 is the milestone that makes the thesis story
complete; C2/C3 are the frontier.

---

## Workstream D — Repo Access & Credentials

Today: anonymous clone only, zero write capability (no PAT/App/`create_pull`
anywhere). This workstream is the enabler for private-repo analysis and for C's
PR creation. It is a **security surface**, not a config change.

**D1 — PAT-based read for private repos.**
Lower risk, read-only. Authenticated clone of private repos via a PAT (or
fine-grained token). Establishes token handling discipline: injected from a
secrets source, **never** written to the job store, logs, or artifacts (note:
the environment already guards credential exposure — the same discipline
applies here). Enables analyzing the user's own private projects.

**D2 — Write access + PR creation.**
A `create_pull` port + adapter (fits the hexagonal layout). Fine-grained token
or GitHub App scoped to the target repo. Branch strategy: branch when we have
write access, fork otherwise.

**D3 — Consent & audit model.**
Per-job explicit opt-in before any write. Minimal token scope. An audit trail
of exactly what was written and why. This is the trust contract that makes
auto-remediation responsible rather than reckless.

Chunks: D1 (read-private via PAT), D2 (write + PR adapter), D3 (consent/audit).
D1 can land independently and has standalone value (private-repo analysis).
D2/D3 are prerequisites for C4.

---

## Sequencing & milestones

1. **M1 — Trustworthy analysis** (A1–A4 + B1–B3): non-determinism root-caused
   and bounded, fixture corpus live, determinism metric reported. *This is the
   gate: don't build C beyond C0 until M1.* Also delivers the honest
   reliability numbers the thesis needs.
2. **M2 — Private-repo analysis** (D1): PAT read; analyze private projects.
   Independent, ships value early.
3. **M3 — The closed loop, reliably** (C0 + C1 + D2 + D3 + C4 for Tier 0/1):
   verified dependency-bump PRs, including transitive-fix-via-direct-bump, with
   the "finding is gone" re-analysis. *This is the headline capability and the
   complete investigate→recommend→remediate→verify story.*
4. **M4 — The frontier** (C2, then C3): verified code-migration and
   package-replacement, evaluated honestly against the fixture corpus.

---

## Risks & non-goals

- **Trust inversion (C).** A wrong recommendation costs 30 seconds; a
  wrong-but-plausible auto-PR can be merged and break production. Verification
  gate is non-negotiable; unverified changes are never proposed.
- **Test-coverage dependency (C2/C3).** The safety net is the target's tests,
  which we don't control. "It builds" barely implies "behavior preserved." The
  feature's reliability is explicitly bounded by target test quality — stated,
  not hidden.
- **Determinism ceiling (A).** Multi-agent tool-calling won't be fully
  deterministic. Goal is bounded, measured variance — not a false promise of
  reproducibility.
- **Not competing with Dependabot/Snyk on scanning.** Tier 0 alone reinvents
  Dependabot; don't frame the thesis there. The novel envelope is Tier 1+
  reasoning, Tier 2/3 migration, and the verification gate.
- **Fixture realism vs. safety (B).** Never plant real malware in
  `fixture-supply-chain`; use benign-but-flaggable signals (a harmless
  postinstall script, near-typosquat name).

## Open questions

- Determinism target: what Jaccard stability across runs is "good enough" to
  gate C? (Set the threshold once A4 produces baseline numbers.)
- Fixture hosting: dedicated org vs. repos under the existing account; who owns
  them long-term.
- Token model for D: personal PAT (simplest, thesis-appropriate) vs. GitHub App
  (productiony, more work). Likely PAT for the thesis.
- Where remediation output lives when C runs without D yet: diff artifact in
  the report vs. a downloadable patch — so C0/C1 can be demoed before D lands.
