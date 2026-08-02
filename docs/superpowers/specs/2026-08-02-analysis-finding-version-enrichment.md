# Spec: Analysis Subgraph — Structured Version Fields on FindingNote

**Date:** 2026-08-02 (draft — to be picked up in a later session)
**Scope:** Backend only (`apps/backend`), limited to
`src/models/conductor.py::FindingNote` and
`src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py`. Remediation
subgraph consumption of these fields is a separate, smaller follow-up (see
`2026-08-02-remediation-tier-classification.md`, D1b).

## Context

While designing `2026-08-02-remediation-tier-classification.md`'s tier
classifier, remediation's own `npm_audit`/`npm_outdated` calls were traced to
see whether they duplicate the analysis subgraph's Trivy scan
(`9ade069`, "Adopt Trivy for vulnerability, license, and dependency-graph
scanning"). Finding: Trivy's own JSON already carries `InstalledVersion` and
`FixedVersion` per vulnerability (`trivy_vuln_scan`,
`apps/backend/src/main_graph/tools/trivy_cli.py:87-96`), but
`parse_trivy_vuln_findings` (`trivy_vuln_parser.py:22-59`) only ever embeds
them as free text inside `FindingNote.description`/`evidence[].log_snippet`
— there are no structured fields, and no breaking-change indicator at all.
That's what pushed remediation to shell out to `npm audit`/`npm outdated`
itself (for `isSemVerMajor` and current/wanted/latest) instead of reading
this data off the finding it already has. Decision: that's duplicated work
in the wrong subgraph — the version data already exists in Trivy's raw
output at the point `FindingNote`s are built; it should be captured
structurally there instead of re-derived later via a second tool.

## Decisions

- **D1 — Add `installed_version: str | None`, `fixed_version: str | None`,
  and `is_semver_major: bool | None` to `FindingNote`.** All three default to
  `None` so non-Trivy-sourced findings (if any other agent ever produces a
  `FindingNote`) remain valid without change. `None` on a Trivy-sourced
  finding specifically means "not computable" — e.g. `FixedVersion` absent
  ("no fix available") or either version string not parseable as semver —
  never a silent false negative for `is_semver_major`.
- **D2 — Compute `is_semver_major` deterministically in
  `parse_trivy_vuln_findings`, from Trivy's own already-fetched
  `InstalledVersion`/`FixedVersion` strings.** No new tool call, no npm CLI
  involvement, no LLM: parse each as semver, compare major segments. This
  is intentionally not a port of npm audit's own `isSemVerMajor` flag (that
  would still require calling `npm audit`) — it's an independent,
  self-contained computation from data Trivy already returns.
- **D3 — Non-vulnerable-but-outdated packages remain out of scope for
  `FindingNote`.** `npm outdated`'s coverage of packages with no CVE (just
  stale) doesn't fit `FindingNote`'s purpose (a finding is a risk, not mere
  staleness) and isn't something Trivy reports either. Remediation's target
  selection already only operates on findings (`select_remediation_targets`
  takes `analysis.findings`), so this was never used to grow the target set
  — only as extra LLM context, which classify_targets_node's release-notes
  read already covers reasonably well without it.

## Out of scope (for this draft)

- Wiring these fields into `RemediationTarget`/`classify_targets_node` — that
  follow-up is noted in `2026-08-02-remediation-tier-classification.md` D1b
  and should be scoped once this spec is picked back up.
- Any other finding source besides Trivy vuln scanning (license, dependency
  graph, etc.) — those parsers are untouched.
- Backfilling historical/already-persisted `FindingNote` records — new field
  defaults handle old records fine on read.

## Success criteria (for this draft)

- `FindingNote` gains the three optional fields with no breaking change to
  any existing caller (all default `None`).
- `parse_trivy_vuln_findings` populates them correctly for a fix-available
  major bump, a fix-available minor/patch bump, and a no-fix-available
  vulnerability (all three exercised in tests, matching the existing test
  style for that parser).
