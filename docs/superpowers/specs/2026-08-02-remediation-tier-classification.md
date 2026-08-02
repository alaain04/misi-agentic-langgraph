# Spec: Remediation Subgraph — r1/r2/r3 Tier Classification

**Date:** 2026-08-02
**Scope:** Backend only (`apps/backend`), limited to the internals of
`remediation_subgraph` (`src/main_graph/subgraphs/remediation/`). Discovery,
analysis, and report subgraphs are untouched.

## Context

`docs/superpowers/specs/2026-07-26-remediation-deepagent-tier-ladder.md`
(implemented in PR #30) built the current shape: `select_remediation_targets`
seeds a target set, `root_deepagent_node` dispatches one `remediate_target`
subagent per target, each subagent decides `strategy` (`bump` /
`bump_with_codemod` / `replace`) from release notes + blast-radius/usage
evidence, `group_and_verify_gate` replays and verifies connected groups
(coupled via a `requires` signal) from a clean clone, and `pr_and_persist_node`
opens one PR per verified-green group.

The user wants these three strategies treated as three explicit remediation
tiers — r1 (minor bump), r2 (breaking change, needs code adaptation), r3
(dependency migration/replacement) — with a specific behavior change: r3 is
postponed project-wide for now (flagged, never executed), while r1/r2 keep
today's full investigate-and-fix behavior, each still landing in a
tier-labeled, per-group PR as today.

## Decisions

- **D1 — A new `classify_targets_node` runs once, before dispatch, and
  replaces `root_deepagent_node`'s current inline initial-selection branch.**
  Today's `select_remediation_targets` call, currently inlined in
  `root_deepagent_node`'s `else` branch (the non-retry path), moves into its
  own node, unchanged. It additionally fetches each target's GitHub release
  notes and makes one structured-output LLM call per target classifying
  `tier: r1 | r2 | r3` with a short `rationale`, from the release notes text
  and the target's known current range alone. This runs only on the initial
  pass — retries re-enter `root_deepagent_node` directly (unchanged), never
  re-classify. Reason for a *separate, cheap* classification step rather than
  letting the existing full subagent decide everything as it does today: it
  lets r3 targets be filtered out before paying for the expensive
  investigate-and-edit subagent run at all, since they're deferred regardless
  of what that investigation would find.
- **D1b — `npm_audit`/`npm_outdated` are dropped from remediation entirely,
  not relocated.** They are not called by `classify_targets_node`, and
  `state["evidence"]` and the `else`-branch calls that built it are removed
  from `root_deepagent_node` outright. `subagent_wrapper.py`'s per-target
  prompt drops its "Evidence (npm audit fix paths, outdated versions)"
  section accordingly — the subagent still gets everything else unchanged
  (its own `read_release_notes` call, `blast_radius`, `search_code`).
  Rationale (from review discussion): this data is largely redundant with
  what the analysis subgraph's Trivy scan already computed for each finding
  (installed/fixed version), just not yet in structured form on `FindingNote`
  — re-deriving it via a second tool (npm CLI) inside remediation duplicates
  work that belongs in analysis. The one real signal this gives up for now —
  npm audit's `isSemVerMajor` breaking-change flag, and npm-outdated's
  non-vulnerable-but-stale coverage — has no replacement in *this* spec; it's
  the explicit subject of the companion spec
  `docs/superpowers/specs/2026-08-02-analysis-finding-version-enrichment.md`
  (drafted, not yet built). Until that lands, tier classification here relies
  on the LLM's own reading of the release notes prose (which commonly states
  breaking changes explicitly) rather than a structured flag. A small
  follow-up to this spec will wire the enriched fields into
  `classify_targets_node`'s prompt once the companion spec ships.
- **D2 — r3-classified targets never reach `root_deepagent_node`; they get an
  upfront settled `Remediation` record instead.** For each target classified
  r3, `classify_targets_node` writes
  `Remediation(target_dep=..., addresses=..., from_range=current_range,
  strategy="replace", status="skipped", skip_reason="dependency migration -
  deferred, not yet supported")` directly into `state["remediations"]`
  (merges via the existing `_merge_replace` reducer). Only r1/r2 targets are
  included in the `targets` dict the node returns, which is what
  `root_deepagent_node` dispatches and what `group_and_verify_gate` uses to
  compute `target_deps` for connected-group grouping.
- **D3 — `group_and_verify_gate` gains one new check: any group containing a
  `strategy == "replace"` member is deferred wholesale, never verified.**
  This is the single mechanism that enforces "a group needing an r3 companion
  is entirely deferred," and it fires identically whether the `replace`
  member got there via D2 (pre-classified, never dispatched) or emerged
  mid-investigation from an r1/r2 subagent that independently concludes a
  migration is actually needed (today's existing, unchanged agent judgment —
  see D7 of the prior spec). In both cases the check is: before attempting
  `replay_and_verify_group` for a group, if any member (including one pulled
  in only via a `requires` edge, per `connected_groups`' existing
  required-only-member support) has `strategy == "replace"`, skip
  verification entirely and set every member's `status = "skipped"` with
  `skip_reason = "coupled to a dependency migration (r3) target - deferred"`
  — overriding even a member that would otherwise have verified green. A
  **mixed r1+r2 group** (no r3 involved) is not deferred — it proceeds
  through today's verification unchanged, and naturally promotes to the
  r2-labeled PR via D5's existing strategy-set logic.
- **D4 — Nothing about today's existing `replace` plumbing is removed.**
  `apply_group_changes`' replace branch, `Remediation`/`RemediationOutcome`'s
  `replacement_dep`/`replacement_range` fields, and `_pr_title_and_body`'s
  "replace - review required" label all stay exactly as they are. They are
  unreachable through the automated path today (D3 defers every group before
  they'd matter), but they are the correct foundation for the future r3
  implementation (D6) and represent already-working logic that must not
  regress.
- **D5 — PR granularity and tier labeling are unchanged: still one PR per
  connected group.** `pr_and_persist_node` already only opens a PR when every
  member of a group has `status == "fixed"` — a deferred (D3) group
  automatically yields zero PRs, no new logic needed there. `_pr_title_and_body`
  already derives its label from the group's `strategy` set (`bump` →
  "bump", `bump_with_codemod` present → "codemod - review required"), which
  already gives a same-tier group the right label and a mixed r1+r2 group the
  r2 label for free. No PR consolidation across groups (explicitly rejected —
  a run with 3 unrelated r1 fixes still yields 3 separate PRs, each tier
  labeled).
- **D6 — Future work note (not built now): r3, when implemented, needs its
  own dedicated subagent, not an extension of `remediate_target`.** Migrating
  to a replacement package requires investigating the *candidate* package's
  own documentation/migration guides — a distinct research task from
  `remediate_target`'s current tools (`read_release_notes`, `blast_radius`,
  `search_code`), which are oriented entirely around understanding the
  *current* package and its own changelog. The future implementation should
  add a parallel `migrate_target` subagent (mirroring `build_target_subagent`)
  with its own prompt/tools for that. Recorded here so the reasoning isn't
  lost; no scaffolding is added in this change.

## Data flow

```
START
  -> classify_targets_node
       - select_remediation_targets (unchanged, deterministic) -> initial targets
       - no npm_audit/npm_outdated call (D1b - dropped from remediation)
       - per target, concurrently: fetch release notes + one structured LLM
         call -> tier (r1/r2/r3) + rationale, from release notes text alone
       - r3 targets: write a settled Remediation(strategy="replace",
         status="skipped", skip_reason=...) straight into `remediations`;
         excluded from the `targets` dict returned
       - r1/r2 targets: included in `targets`, unchanged shape from today
  -> root_deepagent_node
       - dispatches remediate_target subagents for `targets` (r1/r2 only)
         exactly as today; retry path unchanged (never re-classifies)
  -> group_and_verify_gate
       - NEW: any group with a strategy=="replace" member (pre-classified or
         emergent) -> every member status=skipped, reason recorded, no
         verification attempted
       - otherwise: today's replay + verify + correction-round loop, unchanged
  -> pr_and_persist_node
       - unchanged: one PR per group where every member is status=="fixed",
         tier-labeled via existing strategy-set logic
  -> END
```

## Out of scope

- Actually building r3 (migration) remediation — deferred per explicit
  request; tracked as future work (D6).
- A dedicated `migrate_target` subagent — future work, not scaffolded now.
- Consolidating PRs across groups or across the whole run — rejected (D5).
- Any change to `discovery`, `analysis`, or `report` subgraphs.
- Surfacing deferred r3 findings more prominently in the API/report response
  than today's existing `status`/`skip_reason` fields already do — the data
  is present; a dedicated UI/API affordance is a separate, smaller follow-up.
- Enriching `FindingNote` with structured `installed_version`/`fixed_version`/
  `is_semver_major` fields — that's the companion spec
  `2026-08-02-analysis-finding-version-enrichment.md` (drafted, to be built
  later). This spec's classify step ships without that signal for now (D1b).

## Success criteria

- r1/r2 targets behave exactly as today end-to-end (investigate, decide,
  edit, verify, PR) — this change must not regress the prior spec's shipped
  behavior for the common case.
- An r3-classified target never reaches `root_deepagent_node` / the full
  subagent, and always ends up `status="skipped"` with a migration-deferred
  reason, with zero PR.
- A group containing an r3 member (pre-classified or discovered mid-run) is
  deferred as a whole — no partial PR ships a coupled fix without its
  migration-needing companion.
- A mixed r1+r2 group (no r3 involved) still verifies and ships as one
  PR, tier-labeled by its highest tier (r2).
- `classify_targets_node`'s tier split and `group_and_verify_gate`'s new
  defer-on-replace check are pure/deterministic given their LLM-classified
  inputs and unit-tested without a live LLM (scripted fake model / fake
  classification results), matching the existing test style for
  `selection.py` and `grouping.py`.
- Full backend suite, ruff, and mypy green.
