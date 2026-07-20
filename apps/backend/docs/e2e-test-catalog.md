# E2E Test Catalog — Analysis Pipeline Quality Validation

A curated set of real end-to-end runs against a live backend (`POST /analyze` →
poll `GET /analyze/{trace_id}` → inspect the report), covering different
repos, package managers, and concerns. Unlike `uv run pytest`, this catalog
validates *finding quality* — is the recommendation actually actionable, is
severity calibrated, is the evidence grounded — not just "did the job reach
`done`".

Written 2026-07-20 as a follow-up to the direct-anchored-findings feature
(`docs/superpowers/plans/2026-07-20-direct-anchored-findings.md`, root repo
level), whose recommendation-anchoring and lockfile-fallback behavior gets
its own dedicated section (Group 3) — but this catalog is meant to be a
durable, reusable reference for validating the pipeline generally, not
scoped to one PR.

**Status:** written, not fully executed. Group 3 / Test 3.1 (chalk +
vulnerability concern) has been run twice live during feature development —
see its row for the actual results. Everything else is planned but unrun;
fill in the Results column as each is executed.

---

## How to run one test

### Option A — curl, manual

```bash
# 1. Ensure prerequisites: MongoDB running, Docker running, backend started
cd apps/backend
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 &

# 2. Submit
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "<REPO_URL>",
    "concern": "<CONCERN TEXT>",
    "autopilot": true
  }' | tee /tmp/submit.json

# 3. Poll (autopilot=true skips the hitl_gate; status goes pending -> running -> done)
TRACE_ID=$(python3 -c "import json;print(json.load(open('/tmp/submit.json'))['trace_id'])")
for i in $(seq 1 40); do
  RESP=$(curl -s "http://localhost:8000/analyze/$TRACE_ID")
  STATUS=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['status'])" "$RESP")
  echo "[$i] status=$STATUS"
  [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] || [ "$STATUS" = "cancelled" ] && echo "$RESP" > /tmp/final.json && break
  sleep 15
done

# 4. Inspect the report (relaxed JSON parsing — finding text can contain control chars)
python3 -c "
import json
d = json.loads(open('/tmp/final.json','rb').read().decode('utf-8'), strict=False)
report = next(a for a in d['artifacts'] if a['node']=='report')['output']
print('overall_risk_level:', report.get('overall_risk_level'))
print('findings:', len(report.get('findings', [])))
for f in report.get('findings', []):
    print(f.get('dep_name'), '| severity=', f.get('severity'), '| is_direct=', f.get('is_direct'), '| direct_dependents=', f.get('direct_dependents'))
    print('  rec:', f.get('recommendation'))
"
```

### Option B — httpYac

`http-docs/analyze.http` has the same request pre-built (edit `repo_url` /
`concern` inline, or add new named requests for each test case below).

### Option C — scripted (structural checks only, no quality judgment)

`scripts/e2e_check.py --repo <url> --concern <text> --timeout 900` — automates
submit+poll+basic structural assertions (`overall_risk_level` set,
`recommendations` non-empty when findings exist). It predates this feature
and does not check `is_direct`/`direct_dependents`/anchoring — use it for a
quick smoke pass, not for the quality checks in this catalog. Worth updating
with the checks in Group 3 if this catalog proves out.

### Inspecting the dependency graph directly (when the report alone isn't enough)

Useful for diagnosing *why* `direct_dependents` came back empty/wrong — check
whether `install_deps` actually produced a lockfile:

```bash
cd apps/backend
uv run python3 -c "
from pymongo import MongoClient
db = MongoClient('mongodb://localhost:27017/misi-langgraph').get_default_database()
prep = db['prep_results'].find_one({'job_id': '<TRACE_ID>'})
dg = prep.get('dependency_graph', {})
print('pm:', prep.get('detected_package_manager'))
print('direct:', len(dg.get('direct', {})), 'packages:', len(dg.get('packages', {})))
"
```

Also check the backend log for `install_deps: lock_created=<bool>` and (if
the fallback fired) `--package-lock-only` / `--lockfile-only` in the logged
command.

---

## Quality rubric

Apply this checklist to **every finding** in every run, not just the
structural pass/fail. A run can have `status: done` and still fail quality.

| # | Check | Why it matters |
|---|-------|-----------------|
| Q1 | `recommendation` names a specific action on a specific package — not generic filler like "review manually" (that string is the coded fallback when the LLM never succeeds; seeing it live means something upstream failed silently) | Actionability is the whole point of the report |
| Q2 | For `is_direct: false` findings: recommendation names the direct dependent(s) from `direct_dependents`, not the transitive package itself | Core invariant of the direct-anchored-findings feature |
| Q3 | For `is_direct: false` findings: recommendation never says "replace/patch/pin/override `<transitive>`" | Same invariant — the user has no lever on a transitive package directly |
| Q4 | `alternatives` never contains the transitive package's own name, and is empty unless a real replacement is being proposed for a *direct* dependency | Alternatives only make sense for something in `package.json` |
| Q5 | Maintenance-domain findings (`description`/context about staleness, abandonment, download trends) only ever appear with `is_direct: true` | D1 — the core scoping rule |
| Q6 | Severity is proportionate to the evidence in `description`/`evidence` — no `critical` backed by a vague, unsourced claim | Prevents severity inflation |
| Q7 | No duplicate findings for the same `dep_name` describing the same underlying issue | Signal, not noise |
| Q8 | `business_impact` / `blast_radius.narrative`, when present, is grounded in real file paths (`affected_files`), not generic boilerplate | Enrichment agent must use its tools, not invent |
| Q9 | When the finding's `description` embeds an audit fix path ("Fix requires X@Y"), the recommendation uses that exact package/version, not a different guess | D5 |
| Q10 | `direct_dependents` is non-empty for `is_direct: false` findings **whenever** the job's `dependency_graph.packages` is non-empty (cross-check via the MongoDB query above) — empty is only correct when there's genuinely no transitive data | Catches silent fan-out regressions |
| Q11 | `overall_risk_level` equals the max severity actually present in `findings` | Basic report-level consistency |
| Q12 | Cost (`artifacts[].cost`, summed) is in a sane range for the repo size / concern breadth — flag if a small repo costs as much as a large one (runaway ReAct loop) | Cost regression signal |

---

## Group 1 — Core feature validation (single repo, concern variations)

Reuses `chalk/chalk` across concerns to isolate *concern-sensitivity* from
*repo-variability* — cheap to run repeatedly since clone+install is small,
and this repo is already confirmed to exercise the lockfile-fallback path
(no committed lockfile) plus a real multi-parent transitive
(`minimatch` ← `ava`, `c8`, `xo`).

| # | Concern | Expected agent dispatch | Expected quality signal | Result |
|---|---------|--------------------------|--------------------------|--------|
| 1.1 | "Check for outdated or unmaintained dependencies, and any known security vulnerabilities in the dependency tree" | `maintenance_agent` + `vulnerability_agent` | Maintenance findings (`matcha`, `yoctodelay`) always `is_direct: true`; transitive vuln findings (`electron`, `@typescript-eslint/*`, `minimatch`) `is_direct: false` with `direct_dependents` populated and recommendation naming them | **PASS (run twice, 2026-07-20)** — see PR #20 description for full evidence. First run found+fixed the lockfile-fallback bug; second run confirmed `minimatch` → `["ava","c8","xo"]` fan-out with correct recommendation |
| 1.2 | "Are any dependencies unmaintained or abandoned?" (maintenance-only, no vuln/security wording) | `maintenance_agent` only (verify conductor doesn't also dispatch `vulnerability_agent` unprompted) | Every finding `is_direct: true` (Q5); zero maintenance findings on `electron`/`@typescript-eslint/*`/`minimatch` even though those exist in the tree | **PASS (2026-07-20, trace `6a5e4b55c8151f9c621925f6`)** — only `maintenance_agent` dispatched (no unnecessary vuln check). 2 findings (`matcha`, `yoctodelay`), both `is_direct: true`. Quality flag: `yoctodelay`'s recommendation is the coded fallback string "Review manually" (Q1) — enrichment failed for that finding, not a directness-feature defect but worth tracking if it recurs |
| 1.3 | "Are there any license compatibility issues that would restrict commercial or open-source use?" | `license_agent` | `license_agent` runs full-tree per its deterministic design; check whether any transitive license finding correctly anchors its recommendation on a direct dependent (Q2/Q3) — this agent wasn't covered by the earlier live validation, which only exercised maintenance+vulnerability | **PASS structurally (2026-07-20, trace `6a5e4c88c8151f9c621925fb`)** — only `license_agent` dispatched. **0 findings**, `overall_risk_level: none` — chalk's tree is license-clean (expected, well-known MIT ecosystem package). Doesn't exercise D2/D3 for this agent; needs a repo with a real GPL/copyleft transitive dependency for a meaningful check — not yet identified |
| 1.4 | "Could any dependency be a supply-chain risk — typosquatting, malicious install scripts, suspicious maintainers?" | `supply_chain_agent` | Check `resolve_transitive_parent` tool output (used by this agent) is consistent with the enricher's own `direct_dependents` — two independent mechanisms computing the same thing, per the whole-branch review's Minor note in PR #20 | **PASS structurally (2026-07-20, trace `6a5e4d82c8151f9c62192600`)** — `supply_chain_agent` dispatched twice, heavily used `resolve_transitive_parent`. **0 findings** — chalk is supply-chain-clean (expected). Same limitation as 1.3: confirms the tool runs correctly but no finding exists to validate anchoring against |
| 1.5 | "Is this package safe to use in production?" (broad, no domain hint) | Conductor's choice — record what it actually dispatches | Judge whether the conductor's breadth decision is reasonable (2+ agents, not just 1); this is the least deterministic test in the catalog, expect to *read* the `reasoning` field on `AnalysisConductorDecision` via the `analysis` artifact rather than assert a fixed agent list | **PASS (2026-07-20, trace `6a5e4ea8c8151f9c62192606`)** — conductor dispatched `vulnerability_agent` + `maintenance_agent` + `supply_chain_agent` (reasonable breadth for a vague concern, not over- or under-dispatching). 10 findings, no duplicates this time, `minimatch` fan-out again correct (`["ava","c8","xo"]`), all directness fields correct. Total cost $0.119 |

---

## Group 2 — Package manager coverage

| # | Repo | Package manager | Lockfile committed? | What this validates | Result |
|---|------|-------------------|----------------------|------------------------|--------|
| 2.1 | `expressjs/express` | npm | Yes (`package-lock.json` in repo) | **Happy path** — `install_deps` should NOT need the `--package-lock-only` fallback (`lock_created=True` on the first attempt); `direct_dependents` populated immediately. Contrast with 1.1/chalk where the fallback *is* needed | not yet run |
| 2.2 | `chalk/chalk` | npm | No | Fallback path (see Group 1 — already validated) | done (see 1.1) |
| 2.3 | a small pnpm-based repo (verify current lockfile at execution time, e.g. an `unjs/*` utility repo) | pnpm | Yes | pnpm parser (`_parse_pnpm_lock`) on real data; confirm `detected_package_manager: "pnpm"` and non-empty `packages` | not yet run |
| 2.4 | a yarn Classic repo (verify `yarn.lock` has no `__metadata` key at execution time — that's the Classic/Berry discriminator) | yarn (Classic) | Yes | `_parse_yarn_classic_lock`; also confirms Group-2's yarn case has **no** lockfile-only fallback available (by design — see Task in the install_deps fix) so a repo here without a committed lock would need a different validation; prefer one that already has `yarn.lock` checked in | not yet run |
| 2.5 (P1) | a yarn Berry repo (`__metadata` key present in `yarn.lock`) | yarn (Berry) | Yes | `_parse_yarn_berry_lock` — lower priority since Berry adoption is less common; skip if no small stable example is found | optional |
| 2.6 (P1) | a small pnpm/yarn **workspaces** monorepo | pnpm or yarn | Yes | `direct` dict scope — does it correctly reflect only the target package.json's own deps, or does workspace hoisting confuse `root_keys`? Edge case worth one spot-check | optional |

---

## Group 3 — Direct-anchored-findings deep checks

These aren't separate jobs — they're the specific assertions to run against
**every** job in Groups 1 and 2, pulled out here because they're the reason
this catalog exists. Re-run the Quality Rubric's Q2/Q3/Q4/Q5/Q9/Q10 against
each completed job and log any failure here with the trace_id.

| # | Assertion | Source | Result |
|---|-----------|--------|--------|
| 3.1 | No finding's `recommendation` text tells the user to replace, patch, pin, or override the transitive package itself when `is_direct` is `false` for that finding | Q3 | PASS on 1.1 (2 runs) |
| 3.2 | Every transitive finding under a shared package lists **all** its real direct dependents, not just one | Q10 / D4 | PASS on 1.1 run 2 (`minimatch` → 3 dependents) |
| 3.3 | `install_deps` fallback fires exactly when needed and never loops/retries more than once | covered by unit tests (`test_install_deps_*_falls_back_to_lockfile_only`), but worth confirming once live per package manager | PASS for npm (1.1); pnpm/yarn fallback is npm-and-pnpm-only by design (yarn has none) — no live pnpm fallback case validated yet (2.3's repo is expected to already have a lockfile, so the fallback won't trigger there either; a *live* pnpm-fallback validation needs a pnpm repo with no committed lockfile, not yet identified) |
| 3.4 | Maintenance findings are absent for every transitive package with real evidence of staleness available (i.e. not just "none happened to be flagged") — cross-check by manually running `unmaintained_packages`/`high_risk_packages` against a known-stale transitive in the target repo and confirming the agent still doesn't surface it as a finding | Q5, strongest form | not yet run — needs a repo with a deliberately old/abandoned transitive dependency to be a meaningful test, not just an absence-of-evidence result |

---

## Group 4 — Edge cases / negative tests

| # | Input | Expected behavior | Result |
|---|-------|---------------------|--------|
| 4.1 | `repo_url` that 404s (e.g. `https://github.com/octocat/this-repo-does-not-exist-xyz`) | Job reaches `status: failed` with a populated `error` field; no partial/garbage report; `clone_repo` node's failure routes correctly (see `_route_after_clone` in `discovery/graph.py`) | not yet run |
| 4.2 | A repo with no `package.json` at the root (e.g. a docs-only or non-JS repo) | Graceful degrade — `read_package_json` returns `{}`, `direct` is empty, pipeline still completes with `overall_risk_level: none` rather than crashing | not yet run |
| 4.3 | A repo with a `package.json` but zero dependencies (`{"name": "x", "version": "1.0.0"}`) | `direct: {}`, no findings, `overall_risk_level: none`, no maintenance/vuln agent dispatch (or a dispatch that trivially returns nothing) | not yet run |
| 4.4 | A large monorepo (e.g. a repo with 1000+ packages in its tree) | Job either completes within a generous timeout or fails cleanly — not a silent hang; watch `_MAX_TREE_DEPTH` (15) and `_MAX_ITERATIONS` bounds in the conductor/agents; note total cost (Q12) | not yet run |
| 4.5 | A private repo the backend's git credentials can't access | `clone_repo` fails with a clear error, job → `failed`, not an unhandled exception | not yet run |

---

## Group 5 — HITL flow (autopilot=false)

Everything above uses `"autopilot": true` to skip the gate. At least one run
should exercise the real HITL path.

| # | Test | Expected | Result |
|---|------|----------|--------|
| 5.1 | Submit with `"autopilot": false` on a repo/concern likely to trigger a conductor checkpoint | Job reaches `status: awaiting_approval`; `artifacts` contains a `hitl_gate` node with `status: running` and a `messages` entry with `role: assistant`, `type: "ask_user"` or `"checkpoint"` (per `docs/backend/hitl.md`); `POST /analyze/{trace_id}/chat` with a reply resumes to `running` (202) then eventually `done` | not yet run |
| 5.2 | Same as 5.1 but reply with something that should redirect/cancel the investigation | Job responds sensibly — either narrows scope or reaches `cancelled`/`done` with reduced findings, not stuck | not yet run |

---

## Group 6 — Cost & performance sanity

Not pass/fail — record and watch for outliers over time.

| # | Metric | How to check | Baseline (from 1.1) |
|---|--------|---------------|------------------------|
| 6.1 | Total job cost | Sum `artifacts[].cost` from the final status response | chalk run 1: prep $0.0003, analysis $0.0096, report $0.164 → **~$0.17 total** |
| 6.2 | Wall-clock duration | `completed_at` minus job creation, or just the polling loop's elapsed time | chalk run 1: ~5.5 min; run 2 (with lockfile fallback, one extra container call): ~5.8 min |
| 6.3 | Per-node timing outliers | `artifacts[].started_at`/`completed_at` deltas | `report` node dominates cost (per-finding enrichment agents) — expected given the per-finding-agent architecture |

---

## Execution log

Append one row per actual run, regardless of which table above it belongs to.
This is the append-only ground truth; the tables above are the plan.

| Date | Test # | trace_id | repo | concern (truncated) | status | Notable findings | Quality issues found |
|------|--------|----------|------|----------------------|--------|-------------------|------------------------|
| 2026-07-20 | 1.1 (run 1) | `6a5e3d464dd81365f98233a6` | chalk/chalk | outdated/unmaintained + vulnerabilities | done | 20 findings (dupes present — enricher retries counted twice, see note) | `direct_dependents` empty for all transitive findings — root cause: `install_deps` produced no lockfile. Fixed in commit `aa138a8` |
| 2026-07-20 | 1.1 (run 2) | `6a5e426977c50f32fbb89366` | chalk/chalk | outdated/unmaintained + vulnerabilities | done | 10 findings, `minimatch` → 3 direct dependents | None — full pass on Q1-Q10 |
| 2026-07-20 | 1.2 | `6a5e4b55c8151f9c621925f6` | chalk/chalk | unmaintained/abandoned only | done | 2 findings, both `is_direct: true` | `yoctodelay` recommendation fell back to "Review manually" (Q1) — enrichment failure for that one finding, not directness-related |
| 2026-07-20 | 1.3 | `6a5e4c88c8151f9c621925fb` | chalk/chalk | license compatibility | done | 0 findings, `overall_risk_level: none` | None found; chalk is license-clean, so this run doesn't exercise transitive-license anchoring |
| 2026-07-20 | 1.4 | `6a5e4d82c8151f9c62192600` | chalk/chalk | supply-chain risk | done | 0 findings, `overall_risk_level: none` | None found; chalk is supply-chain-clean, same coverage gap as 1.3 |
| 2026-07-20 | 1.5 | `6a5e4ea8c8151f9c62192606` | chalk/chalk | broad "safe for production?" | done | 10 findings, no duplicates, `minimatch` fan-out correct | None — full pass; cost $0.119 |

**Open note from run 1:** the first run showed some `dep_name`s appearing
twice in `findings` (e.g. `matcha` twice, `electron` twice) with slightly
different `recommendation` text each time — worth a dedicated look (possibly
the conductor dispatching the same domain agent twice across iterations
without the dedup the system prompt claims, or two different agents both
flagging the same package). Not yet root-caused; add as a follow-up
investigation, separate from this catalog's scope. Runs 1.1(2), 1.2, and 1.5
did not reproduce this (all duplicate-free) — 4 of 5 chalk runs today were
clean, only the very first run (before the lockfile fix) showed duplicates.
Plausible explanation worth checking first in any future investigation: the
first run's two conductor iterations for the same two agents (see its
`agent_calls`) may correlate with the doubling — worth confirming whether
iteration-2 re-dispatch is what produced the duplicates specifically that run.

**Coverage gap identified:** license and supply-chain concerns (1.3, 1.4)
against chalk both returned 0 findings — chalk is simply clean on those axes.
Neither test exercises the direct-anchoring behavior for `license_agent` or
`supply_chain_agent` findings. Needs a repo with a known GPL/copyleft
transitive dependency (for 1.3) or a deliberately-planted/known
typosquat-adjacent case (for 1.4) to close this gap — not yet identified;
add as a follow-up rather than block on finding one now.
