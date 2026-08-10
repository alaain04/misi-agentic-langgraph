# Spec: Remediation PR Description Improvements

**Date:** 2026-08-08
**Scope:** Backend only (`apps/backend`), limited to the PR body generator in
`src/main_graph/subgraphs/remediation/deepagent/nodes.py`
(`_PR_BODY_TEMPLATE` and its `_pr_*` helpers), `src/models/remediation.py`
(`RemediationTarget`, `Remediation`), and
`src/main_graph/subgraphs/remediation/selection.py`
(`select_remediation_targets`).

## Context

Reviewing a real generated PR body surfaced three issues:

1. The body opens with `Automated dependency remediation ({label}).` even
   though the actual GitHub PR title (built separately in
   `_pr_title_and_body`, `"Remediate {deps} ({title_label})"`) already says
   this — the line is a redundant second title.
2. The `## Findings addressed` table (`_pr_findings_table`) only shows
   `| finding_dep_name | resolved_by_dep |` — no indication of what kind of
   risk was resolved (severity, or what the finding actually was).
3. `## Verification (sandboxed container)` exposes an implementation detail
   (the container is a build/verification mechanism, not something the PR
   reader needs to know about) and renders as plain "Install: passed" /
   "failed" prose instead of a scannable checklist.

For (2), traced whether a structured category (vulnerability / license /
obsolescence) is recoverable at the point the PR body is built. It is not:
`FindingNote` (`models/conductor.py`) carries only `dep_name`, `severity`,
`description`, `evidence`, and the Trivy version fields — no domain/category
field. `EvidenceBundle.domain` exists one layer up but is discarded when
`save_analysis_result.py` flattens bundles into `AnalysisResult.findings`
(`[f for b in bundles for f in b.findings]`), and even where `domain` is set
it's inconsistent: the whole-tree path uses a fixed `ConcernType` enum
(`vulnerability`/`license`/`maintenance`/`supply_chain`/`web_research`/
`other`), but the deepagent specialist dispatch path fills it from free-form
LLM text. Adding a reliable category would mean touching the analysis
subgraph's finding-flattening logic, not just remediation's PR generator —
out of scope here.

## Decisions

- **D1 — Drop the redundant title line from `_PR_BODY_TEMPLATE`.** Delete
  `Automated dependency remediation ({label}).` (and the blank line after
  it) from the template. `label` stays a parameter of `_pr_title_and_body`
  (still used for the real PR title and `_pr_summary`'s strategy-review
  line) — only the template string changes.

- **D2 — Carry `severity` + `description` (already on `FindingNote`) through
  to the PR body, instead of inventing a new category field.** Add:
  ```python
  class FindingSummary(BaseModel):
      dep_name: str
      severity: str
      description: str
  ```
  to `models/remediation.py`, and add
  `finding_summaries: list[FindingSummary] = Field(default_factory=list)` to
  both `RemediationTarget` and `Remediation`. Populate it in
  `select_remediation_targets` (selection.py) at the same point `addresses`
  is built from the same `survivors: list[FindingNote]` — one
  `FindingSummary` per finding, grouped under its anchor the same way
  `addresses` already is. `_pr_findings_table` renders a 4-column table:
  `| Finding | Severity | Description | Resolved by |`, with `description`
  truncated to ~150 chars (whitespace collapsed, no embedded newlines) so
  each row stays scannable inside a markdown table cell.

  `finding_summaries` must be threaded through every hop `addresses`
  currently takes without being recomputed:
  - `_resolve_working_targets` (nodes.py) — synthesized retry
    `RemediationTarget` gets `finding_summaries=[]` alongside
    `addresses=[]`.
  - `_assemble_remediations` (nodes.py, all 4 branches) —
    `finding_summaries=target.finding_summaries` alongside each
    `addresses=target.addresses`.
  - `group_and_verify_gate` and `pr_and_persist_node` — no change; both
    rehydrate `Remediation(**dict)` from a dict that already carries the
    field once it's on the model.

- **D3 — Real category tagging (vuln/license/gpl) is out of scope, deferred
  as a follow-up.** It requires adding a domain/category field to
  `FindingNote` and preserving provenance through
  `save_analysis_result.py`'s bundle-flattening — a change to the analysis
  subgraph's data model, not remediation's PR generator. The free-form
  domain values on the deepagent dispatch path would also need
  normalizing/constraining first for the category to be trustworthy in a
  PR description.

- **D4 — Rename `## Verification (sandboxed container)` to `## Verification`
  and render as a GitHub task-list checklist.** Same `VerificationResult`
  fields, same "only show Build/Tests when not `None`" logic. Checked box =
  passed; unchecked = failed, with the failure noted inline:
  ```
  - [x] Install
  - [x] Build
  - [ ] Tests (failed)
  - [x] Audit re-scan — finding no longer present
  ```

## Out of scope

- Category/domain tagging of findings (vulnerability vs. license vs.
  obsolescence) — see D3.
- Any change to `AnalysisResult`, `FindingNote`, or the analysis subgraph.
- Changing `addresses: list[str]`'s type or removing it — `finding_summaries`
  is additive; `addresses` keeps its existing consumers (grouping, targeted
  re-verify dep list in `pr_and_persist_node`) untouched.
- PR title format (`_pr_title_and_body`'s `title` string) — unchanged.

## Success criteria

- Generated PR body no longer contains the `Automated dependency
  remediation (...)` line.
- `## Findings addressed` shows severity (and a description snippet) per
  finding, sourced from `FindingSummary`, with no new LLM/tool call
  introduced.
- `## Verification` (renamed) renders as checkboxes reflecting the same
  pass/fail/omitted semantics as today.
- Existing remediation subgraph tests
  (`tests/unit/subgraphs/remediation/test_deepagent_nodes.py`,
  `tests/unit/subgraphs/remediation/test_selection.py`, and any test
  covering `_pr_*` helpers) updated for the new template shape and
  `finding_summaries` field; all green.
