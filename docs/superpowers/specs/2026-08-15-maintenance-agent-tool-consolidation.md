# Spec: Maintenance Agent — Consolidate Overlapping Tools into One Raw-Data Tool

**Date:** 2026-08-15
**Scope:** Backend only (`apps/backend`), limited to
`src/main_graph/tools/external_api.py` (the three maintenance-related tools)
and `src/main_graph/subgraphs/analysis/agents/maintenance_agent.py` (tool
list + `system_prompt`). No other agent, subgraph, or model schema changes.

## Context

`MaintenanceAgent` currently uses three tools —
`unmaintained_packages`, `high_risk_packages`, `package_reputation`
(`external_api.py:179-196`, `:270-319`, `:145-172`) — that each independently
fetch npm registry metadata for the same dependencies and apply their own,
inconsistent notion of "risk":

- `unmaintained_packages` flags any package not modified in 365+ days, with
  no awareness of current downloads at all.
- `high_risk_packages` flags packages that are very new (<90 days) or
  abandoned (>730 days), but has an explicit override:
  `has_healthy_downloads` (weekly downloads >= `_LOW_WEEKLY_DOWNLOADS`,
  currently 1000) skips flagging entirely.
- `package_reputation` is a per-package deep dive returning the same
  underlying fields (`created`, `last_modified`, `maintainer_count`,
  `weekly_downloads`, ...) with no flagging logic of its own.

The system prompt's investigation strategy calls `unmaintained_packages` and
`high_risk_packages` first, then `package_reputation` "for packages flagged
by either tool." Because `unmaintained_packages` has no downloads override,
and the prompt's own risk criteria (step 4) never tells the LLM that high
current adoption overrides staleness — only a >50% download *drop* is
mentioned — a package like `class-validator` (last npm release 2022, but
~12M weekly downloads) can still end up flagged as a medium-risk finding.
This is the concrete bug this spec fixes.

Investigation confirmed (via `codegraph_explore` + grep) that all three
tools are used exclusively by `MaintenanceAgent` — no other agent, subgraph,
or script references them — so they can be removed outright rather than
deprecated.

## Decisions

- **D1 — Replace all three tools with a single tool, `package_health_data`.**
  It performs one bulk npm-registry fetch (same `deps[:30]` cap as today) and
  returns raw facts per direct dependency, with no thresholds and no
  flagging: `package`, `created`, `last_modified`, `weekly_downloads`,
  `maintainer_count`, `latest_version`. Entries whose npm metadata fetch
  errored are returned as `{"package": ..., "error": ...}` instead of being
  silently dropped, so the agent can factor "no data" into its confidence
  score (per the existing system_prompt confidence rubric).
  This removes the duplicate network fetches (both old tools independently
  called `_npm_metadata` over the same dep list) and the two conflicting,
  hardcoded staleness cutoffs (365 vs 730 days).
- **D2 — All risk judgment moves to the system prompt; no tool-level
  override remains.** `_LOW_WEEKLY_DOWNLOADS` and the `has_healthy_downloads`
  short-circuit are deleted from `external_api.py` along with the old
  tools. The rewritten prompt explicitly instructs the agent to weigh
  `last_modified` recency against `weekly_downloads` before creating a
  finding, using ~1,000 weekly downloads as the low-adoption anchor (the
  same number the old tool-level override used, now stated as guidance
  rather than enforced in code) and explicitly stating that strong current
  adoption overrides staleness alone. Each `FindingNote`'s description must
  record last release date and weekly downloads so the rationale is
  reviewable.
  **Trade-off accepted:** this removes the deterministic guarantee
  `high_risk_packages` had (a healthy-downloads package could never be
  flagged, proven by `test_high_risk_packages.py`). A contradictory finding
  is no longer structurally impossible, only prompt-discouraged. Accepted
  per explicit direction: tools should be function-specific (single
  responsibility, raw data), and risk synthesis is the agent's job, not
  three overlapping tools' job.
- **D3 — `MaintenanceAgent._agent_tools()` returns `[package_health_data]`
  only.** The investigation strategy collapses from a 3-step
  flag-then-verify sequence to: call `package_health_data` once, then reason
  over the full per-package data set directly — no second per-package
  lookup is needed since the bulk call already returns every field the old
  `package_reputation` step existed to fetch.

## Out of scope

- `typosquat_detection` (`external_api.py:248-264`) — unrelated concern
  (name-similarity, not health/maintenance) and not in `MaintenanceAgent`'s
  tool list; untouched.
- Any change to `MaintenanceAgent.run()`'s post-hoc transitive-finding
  filter (`maintenance_agent.py:82-92`) — orthogonal to which tool produced
  the findings.
- The earlier "use `textwrap.dedent` in `maintenance_agent.py`" idea —
  dropped after confirming `BaseAgent._react_loop` already dedents every
  agent's `system_prompt` centrally (`base_agent.py:213`), and the current
  plain-string style matches `supply_chain_agent.py` / `web_research_agent.py`
  exactly.
- Any deterministic post-hoc safety net (e.g. capping severity when
  downloads are very high) — considered and explicitly declined in favor of
  the prompt-only approach (D2).

## Success criteria

- `unmaintained_packages`, `high_risk_packages`, `package_reputation`, and
  `_LOW_WEEKLY_DOWNLOADS` are removed from `external_api.py`.
- `package_health_data` is registered, returns raw per-package facts for all
  direct deps (capped at 30) in one call, and handles per-package metadata
  errors without dropping the package from the result.
- `MaintenanceAgent._agent_tools()` returns only `package_health_data`, and
  `system_prompt` is rewritten so a repo fixture with a stale-but-high-download
  package (e.g. simulated `class-validator`: `last_modified` from 2022,
  `weekly_downloads` in the millions) does not produce a maintenance
  finding for that package in a manual/E2E check, while a stale
  *low*-download package still does.
- `tests/unit/tools/test_high_risk_packages.py` is replaced by
  `tests/unit/tools/test_package_health_data.py`, covering: raw-facts shape
  for a healthy package, a stale+high-download package (no flagging
  responsibility at the tool level — just confirms the field values are
  returned as-is), and a package whose metadata fetch errors.
- `tests/unit/test_maintenance_agent.py`'s mocked tool-name string is
  updated to `package_health_data` for accuracy.
- `docs/e2e-test-catalog.md:167` is updated to reference
  `package_health_data` instead of the two removed tool names.
- Existing `test_maintenance_agent.py` transitive-filtering tests
  (`test_maintenance_drops_transitive_findings`,
  `test_maintenance_keeps_all_when_no_transitive_data`) continue to pass
  unmodified in behavior (only the mocked tool-name string changes).
