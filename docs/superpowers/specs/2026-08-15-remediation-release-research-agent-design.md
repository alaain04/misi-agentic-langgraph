# Spec: Remediation — Agentic Release-Research Node

**Date:** 2026-08-15
**Scope:** Backend only (`apps/backend`), remediation subgraph:
`src/main_graph/subgraphs/remediation/classify.py`, a new
`release_research.py`, `plan.py` (`_format_targets` only), `state.py`,
`graph.py`, `src/models/remediation.py`, `src/utils/model_registry.py`.
No changes to the execution agent (`deepagent/`) or its own
`read_release_notes` tool, which is a separate, already-working consumer of
`changelog.py`.

## Context

`classify_target` (`classify.py:111-181`) currently does release-note
research and tier classification in a single non-agentic step: it calls
`fetch_release_notes_between` once (a deterministic version-window filter,
capped at 20 releases, each body truncated to 2000 chars, the whole JSON
payload truncated to 6000 chars) and feeds that into one
`with_structured_output` LLM call that decides the tier (r1/r2/r3) **and**
writes `migration_needed` / `migration_guide` / `breaking_changes` in the
same call. That digest becomes `TargetInvestigation.release`
(`ReleaseDigest`), which `plan.py`'s `_format_targets` reads verbatim to
brief the migration planner.

Two concrete problems motivate a change:

1. A single one-shot, truncated call can't follow a release body that says
   "see MIGRATION.md" or links to an external upgrade guide — the actual
   guidance the planner needs is often not in the release body at all.
2. The execution agent (`deepagent/tools.py`'s `read_release_notes`) already
   has precedent for exactly this kind of on-demand, iterative lookup during
   remediation *execution*. Nothing equivalent exists at
   classification/planning time, where the same information would let the
   planner write a better `MigrationPlan` (fewer blind `codemod` tasks,
   fewer missed breaking changes) instead of the execution agent
   discovering gaps mid-run.

Additionally, `_format_targets` (`plan.py:69-83`) never includes
`breaking_changes` in the planner's prompt, even though `classify_target`
already computes it — a pre-existing gap this change closes since it
directly affects whether the new node's output is used.

**Known prerequisite bug (fixed as part of this work, not a design
decision):** `classify.py` on disk currently has an uncommitted, broken
in-flight edit — `_classify_bounded` calls `classify_target` with a 5th
`dependency_graph` argument the function doesn't accept, and
`classify_targets_node` only assigns `targets`/`investigations` inside
`if is_indexed:`, leaving them unbound (crashing with `UnboundLocalError`)
when `_index_codegraph` fails. Both must be fixed before this node can be
wired in, since it depends on `classify_targets_node` always returning a
usable `targets`/`investigations` pair.

## Decisions

- **D1 — New standalone node, not folded into `classify_target` or
  `build_plans_for_targets`.** `classify_targets_node` keeps deciding tier
  (r1/r2/r3) exactly as it does today, using the same single-call, fast
  triage read of `fetch_release_notes_between`. A new
  `research_releases_node` runs **after** classify and **before**
  `build_migration_plan_node`, and only for targets whose `tier` is `r1` or
  `r2` — r3 targets already get a `replace` task from the planner
  regardless of digest content, so a deep research pass adds cost with no
  payoff there. Rationale: keeps the fast/cheap tier decision and the
  deep/expensive research pass as separate, independently-testable
  concerns, rather than one call doing double duty.

- **D2 — `TargetClassification` shrinks to `tier` + `rationale`.**
  `migration_needed`, `migration_guide`, `breaking_changes` are removed from
  `classify.py`'s `TargetClassification` and from the values
  `classify_target` writes into `TargetInvestigation.release`. Classify
  still builds a `ReleaseDigest` for every target (so `TargetInvestigation`
  stays fully populated going into the new node), but with placeholder
  content: `migration_needed=False`, `migration_guide=""`,
  `breaking_changes=[]`. For r3 targets this placeholder is permanent —
  `plan.py`'s r3 path doesn't read those fields. For r1/r2 targets,
  `research_releases_node` overwrites this placeholder.

- **D3 — Agent pattern: reuse `base_agent.py`'s `_react_loop` shape, not
  `deepagents`.** This node only reads (release notes + linked docs) and
  writes a digest — it never edits repo files, so it doesn't need
  `deepagents`' `FilesystemBackend`/virtual-mode machinery the execution
  agent uses. It follows the analysis subgraph's existing pattern instead:
  a structured-output decision per iteration, an explicit `finalize` flag,
  a small iteration cap, tool calls run via `asyncio.gather`. Concurrency
  across targets is bounded by a semaphore, same style as
  `classify.py`'s `_MAX_CONCURRENT_CLASSIFICATIONS`.

- **D4 — Two tools.**
  - `get_release_notes(package_name)` — thin wrapper around the existing
    `fetch_release_notes_between`, passing the `resolved_repo` already
    resolved by `classify_targets_node` (via `RemediationTarget.resolved_repo`)
    to avoid a second `npm view` container spawn, exactly like the execution
    agent's `read_release_notes` tool already does.
  - `fetch_doc(url)` — new. Fetches an arbitrary URL a release body links to
    (a `MIGRATION.md`, `UPGRADING.md`, or external guide). Hardened against
    SSRF given the container runs `run_as_root`: resolve the URL's host
    before connecting and reject private/loopback/link-local/metadata-range
    addresses (RFC1918, `127.0.0.0/8`, `169.254.0.0/16` including the
    `169.254.169.254` cloud metadata address, `::1`); only attach the
    `GH_TOKEN` secret when the resolved host is `github.com` or
    `raw.githubusercontent.com`. Response body capped (matching the
    existing 2000-char-per-release convention) before it reaches the LLM.

- **D5 — Output shape unchanged.** The loop's final structured decision
  produces the same three fields `ReleaseDigest` already has
  (`migration_needed`, `migration_guide`, `breaking_changes`) — no new
  Pydantic fields on `ReleaseDigest` or `TargetInvestigation`. This keeps
  `plan.py`'s consumption code (`_apply_release_digest`, `_format_targets`)
  untouched except for the `breaking_changes` fix in D6.

- **D6 — `_format_targets` gains `breaking_changes`.** Fixes the
  pre-existing gap: the planner's prompt currently never sees
  `breaking_changes` even though it's computed. Added as another line in
  the per-target block, same style as the existing fields.

- **D7 — Failure handling matches `classify_target`'s existing
  convention.** If the loop raises, or exhausts its iteration cap without a
  clean `finalize`, the node falls back to the same conservative default
  `classify_target`'s except-block already uses today:
  `migration_needed=True`, `breaking_changes=["research failed, assuming
  breaking: <exc>"]`, empty `migration_guide`. This keeps the planner
  routing to a `codemod` task rather than silently treating a failed lookup
  as a clean bump.

- **D8 — New `AgentRole.REMEDIATION_RELEASE_RESEARCH`.** Added to
  `model_registry.py`'s `AgentRole` enum, resolved through `get_role_llm`
  like every other role, so cost/latency attribution
  (`agent_role:remediation_release_research` tag) works the same way it
  does for `REMEDIATION_CLASSIFY` and `REMEDIATION_PLAN`.

- **D9 — Prerequisite fix bundled as the first implementation task.**
  Before wiring in the new node, fix `classify.py`'s two known bugs (see
  Context): drop the stray `dependency_graph` argument from
  `_classify_bounded`'s call to `classify_target` (the function body never
  reads it — `_has_no_upgrade` and `compute_blast_radius` use
  `current_range`/`latest_version`/`repo_path` instead — so the argument is
  dead, not a sign of missing plumbing), and make `classify_targets_node`
  initialize `targets`/`investigations` before the `if is_indexed:` branch
  (empty dicts on the `False` path) so downstream nodes never see an
  `UnboundLocalError`.

## State & wiring

`RemediationState.investigations` already uses the `_merge_replace`
reducer (`state.py:17`), so `research_releases_node` only needs to return
the subset of `investigations` it updated (the r1/r2 targets), not the
full dict. `graph.py` gains one edge:
`classify_targets_node -> research_releases_node -> build_migration_plan_node`.

## Out of scope

- Any change to the execution agent's own `read_release_notes` tool
  (`deepagent/tools.py`) — it already does on-demand release-note fetching
  during execution and is untouched by this spec.
- Raising the 20-release page cap or the 2000-char per-release body
  truncation in `fetch_release_notes_between` — D1's placement means this
  node runs only for r1/r2 targets where the existing window is normally
  small; pagination was considered and dropped in favor of the
  linked-docs tool (D4), which covers the actual observed gap (notes
  pointing at external guides, not notes being too long to fit).
- A domain allowlist for `fetch_doc` instead of IP-range blocking — IP-range
  blocking was chosen so legitimate guides hosted anywhere aren't missed;
  an allowlist can be layered on later if abuse is observed.
- Running research for r3 targets — considered and declined (D1); r3's
  `replace` task doesn't consume `ReleaseDigest` content.

## Success criteria

- `classify.py`'s two prerequisite bugs (D9) are fixed and covered by a
  regression test that exercises `classify_targets_node` when
  `_index_codegraph` returns `False`.
- `TargetClassification` has only `tier` + `rationale`; `classify_target`
  no longer produces `migration_needed`/`migration_guide`/`breaking_changes`.
- `research_releases_node` exists, runs only for targets with
  `tier in ("r1", "r2")`, and is wired into `graph.py` between
  `classify_targets_node` and `build_migration_plan_node`.
- `get_release_notes` and `fetch_doc` tools exist; `fetch_doc` rejects a
  private/loopback/link-local/metadata-range URL in a unit test, and a unit
  test confirms `GH_TOKEN` is only attached for `github.com`/
  `raw.githubusercontent.com` hosts.
- A target whose release notes link to an external migration guide produces
  a non-empty `migration_guide` sourced from that guide's content in an
  integration-style test with mocked HTTP/container calls.
- `research_releases_node` failure (mocked exception) produces the same
  conservative fallback shape `classify_target`'s except-block used to
  produce, verified by a unit test.
- `_format_targets` includes `breaking_changes` in its output string;
  existing `plan.py` tests updated accordingly.
- `AgentRole.REMEDIATION_RELEASE_RESEARCH` is registered and resolvable via
  `get_role_llm`; `test_model_registry.py` covers it.
- All existing `test_classify.py`, `test_deepagent_nodes.py`, and
  `test_remediation_subgraph.py` tests pass with the shrunk
  `TargetClassification` and the new node inserted.
