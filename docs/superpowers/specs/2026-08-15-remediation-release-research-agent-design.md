# Spec: Remediation — Replace `classify.py` with Deterministic Selection + Agentic Release-Research

**Date:** 2026-08-15
**Scope:** Backend only (`apps/backend`), remediation subgraph. Deletes
`subgraphs/remediation/classify.py`; adds `subgraphs/remediation/select_targets.py`
and `subgraphs/remediation/release_research.py`; modifies `changelog.py`,
`plan.py` (`_format_targets` only), `graph.py`, `src/utils/dependency_graph.py`,
`src/utils/model_registry.py`. No changes to the execution agent
(`deepagent/`) or its own `read_release_notes` tool, which is a separate,
already-working consumer of `changelog.py`. No changes to the analysis or
discovery subgraphs (considered and declined — see Decisions, D-ENRICH).

## Context

This spec started narrower — add an agentic release-research node alongside
the existing `classify_target` — but investigation surfaced that
`classify.py` on disk is mid-flight on a larger, uncommitted, unrelated
refactor with real bugs (`_classify_bounded` calling `classify_target` with
an argument it doesn't accept; `classify_targets_node` leaving `targets`/
`investigations` unbound when codegraph indexing fails). Tracing why that
argument (`dependency_graph`) existed led to a missing `dependents_of`
helper in a *different* in-flight refactor (discovery's `dependency_graph.py`
relocation to `utils/`). Given the size and uncertainty of untangling that,
the decision was made to remove `classify.py` outright rather than repair
it: selection/version-resolution/tier-check becomes a new deterministic
node, and release-note research becomes the new agentic node originally
proposed. See D-REMOVE and D-SELECT below for the reasoning.

**What `classify.py` did (four responsibilities, now split up):**
1. Target selection/dedup — `select_remediation_targets` (deterministic:
   filter by severity, anchor transitive findings to their direct
   dependent, group by anchor). This function exists, correct and unchanged,
   at git HEAD in the now-deleted (uncommitted) `selection.py`.
2. Version + GitHub repo resolution — `resolve_package_info`.
3. A deterministic "no upgrade exists" check — `_has_no_upgrade` (pure
   version-range comparison, forces tier `r3`, no LLM, no fetch).
4. ONE LLM call that decided tier (r1/r2/r3) **and** wrote
   `migration_needed`/`migration_guide`/`breaking_changes` into
   `TargetInvestigation.release`, from `fetch_release_notes_between`'s
   output (windowed, but see the pagination bug below).

Of these, (4) is the one this spec always intended to replace — a
single-shot, truncated LLM call can't follow a release body that says "see
MIGRATION.md," and the execution agent already has precedent for iterative,
on-demand release-note lookup during execution (`deepagent/tools.py`'s
`read_release_notes`) that classification/planning never had.

**Known bug in the underlying fetch (fixed only for the new node, see
D-TOOLS):** `fetch_release_notes`'s `gh api ... --paginate -q '.[:20]'`
(`changelog.py:106-108`) does not mean "first page, truncated to 20." `gh`'s
`--paginate` follows every `Link: rel="next"` header and concatenates *all*
pages of an array response before running the `-q` filter once — so this
command always fetches a package's *entire* release history, then keeps the
20 most recent. The window filter (`_tag_in_window`) runs *after* that
slice: if the requested upgrade window isn't among the 20 *most recent*
releases, the windowed result comes back empty — not "unfiltered fallback,"
genuinely empty.

Additionally, `_format_targets` (`plan.py:69-83`) never includes
`breaking_changes` in the planner's prompt even though it's computed —
fixed here since it directly affects whether the new node's output is used
(D-FORMAT).

## Decisions

- **D-REMOVE — Delete `classify.py` and `classify_targets_node` outright,
  do not repair the in-flight WIP.** The uncommitted breakage found while
  scoping a "prerequisite fix" turned out to depend on a *second*,
  unrelated in-flight refactor (discovery's `dependency_graph.py` move)
  whose own completeness couldn't be verified without significant
  additional investigation. Repairing both together is a much larger,
  riskier change than this feature needs. Instead: `classify.py`'s four
  responsibilities are redistributed to two new, independently-testable
  nodes (D-SELECT, D-RESEARCH), neither of which needs anything from the
  discovery-side WIP.

- **D-SELECT — New deterministic node `select_targets_node`
  (`select_targets.py`), no LLM.** Runs first in the remediation subgraph,
  replacing `classify_targets_node` entirely. Per target:
  1. `select_remediation_targets(analysis.findings, prep.dependency_graph,
     settings.risk_min_severity)` — ported verbatim from git HEAD's deleted
     `selection.py` (only the dependency-graph-helper import path changes,
     to `src.utils.dependency_graph`).
  2. `resolve_package_info` — version + GitHub repo, bounded concurrency
     (semaphore, same cap as today, 6).
  3. `_has_no_upgrade` — deterministic r3 check, ported unchanged from
     `classify.py`.
  4. Blast radius (`compute_blast_radius`) and structural dependents
     (`dependents_of`, D-DEPENDENTS) for the **anchor** (`target.target_dep`)
     — computed for every target regardless of tier, matching today's
     behavior of still gathering context for r3's replacement plan.
     Codegraph indexing (`_index_codegraph`, ported unchanged) still runs
     once per repo before this step, same as `classify_targets_node` does
     today — the codegraph-index-location question this raised (should it
     move to discovery instead) is resolved by NOT moving it: it stays
     here, because nothing about removing `classify.py` requires relocating
     it, and doing so would mean touching the discovery subgraph, which
     this spec explicitly avoids (D-ENRICH).
  Writes `targets` and `investigations` (with `TargetInvestigation.release`
  as a placeholder `ReleaseDigest` — `migration_needed=False`,
  `migration_guide=""`, `breaking_changes=[]` — for every target;
  `select_targets_node` never sets `tier` to anything but `"r3"` or leaves
  it unset — there is no r1-vs-r2 distinction anymore, decided by nothing
  since `plan.py`'s task-shape branch never read the difference anyway,
  only `migration_needed` and `tier == "r3"` matter downstream), resets
  `remediations`.

- **D-DEPENDENTS — Port `dependents_of` into `utils/dependency_graph.py`.**
  It existed, correct, at git HEAD in the now-deleted
  `discovery/dependency_graph.py`, with tests already present and currently
  failing-on-import in `test_dependency_graph_helpers.py`. Ported verbatim
  (structural "everything that transitively depends on this package,"
  distinct from `direct_dependents`' "which direct deps' subtrees contain
  it" — both are needed: `select_remediation_targets` uses
  `direct_dependents` for anchoring, `select_targets_node` uses
  `dependents_of` for the investigation digest, same split `classify.py`'s
  docs already described).

- **D-ENRICH — Blast-radius/dependents enrichment stays in remediation,
  NOT moved to the analysis subgraph.** Considered and declined: blast
  radius is inherently about the **anchor** (the direct dep actually being
  bumped), which is only known after `select_remediation_targets` groups
  transitive findings to their direct dependent. Analysis produces findings
  before that grouping exists, so enriching `FindingNote` during analysis
  would compute blast-radius for the finding's own (possibly transitive)
  package, not the anchor that's actually getting bumped — wrong data. No
  changes to the analysis or discovery subgraphs are part of this spec.

- **D-RESEARCH — New agentic node `research_releases_node`
  (`release_research.py`).** Runs after `select_targets_node`, for every
  target where `tier != "r3"` (r3 targets get a `replace` task from the
  planner regardless of digest content — a deep research pass adds cost
  with no payoff there). Agent pattern: reuse the analysis subgraph's
  `_react_loop` *shape* (`base_agent.py`) — a structured-output decision
  per iteration, an explicit `finalize` flag, a small iteration cap, tool
  calls run via `asyncio.gather` — not `deepagents`, since this node only
  reads (release notes + linked docs) and writes a digest, never edits repo
  files, so it doesn't need `FilesystemBackend`/virtual-mode. It's a fresh,
  self-contained loop (its own Pydantic decision model, own tools, own
  prompt) rather than a literal call into `base_agent.py`'s `_react_loop`,
  which is tightly coupled to analysis-domain types (`FindingNote`,
  `AgentDispatch`, `critique_findings`). Concurrency across targets bounded
  by a semaphore, same style as `select_targets_node`'s.

- **D-TOOLS — Two tools.**
  - `get_release_notes(package_name, page=1)` — NOT a wrapper around
    `fetch_release_notes_between` (see the pagination bug in Context).
    Fetches one page directly (`gh api
    'repos/{owner}/{repo}/releases?per_page=100&page={page}'`, no
    `--paginate`), passing the `resolved_repo` already resolved by
    `select_targets_node` to avoid a second `npm view` spawn (same dedup
    the execution agent's `read_release_notes` tool already does). Applies
    the existing `_tag_in_window` filter to just that page and returns the
    windowed subset plus `has_more`: true only when the page was full (hit
    `per_page`) *and* its oldest tag is still above the window floor. The
    agent calls again with `page=2`, etc. when it wants more evidence; a
    hard cap inside the tool (independent of the loop's own iteration cap)
    refuses any `page` beyond 10 (~1000 releases).
  - `fetch_doc(url)` — new. Fetches an arbitrary URL a release body links to
    (a `MIGRATION.md`, `UPGRADING.md`, or external guide), as a direct
    Python-side `httpx` call (same pattern as `external_api.py`'s `_get`
    helper — this is metadata/doc fetching, not repo-context work, so it
    doesn't need the sandboxed container). Hardened against SSRF: reject
    non-`http(s)` schemes; resolve the URL's host via DNS before connecting
    and reject any resolved address that is not globally routable
    (`ipaddress.ip_address(ip).is_global` — covers RFC1918, loopback,
    link-local including the `169.254.169.254` cloud metadata address, and
    other IANA special-purpose ranges in one check); only attach the
    `GH_TOKEN` header when the **validated** host is exactly `github.com` or
    `raw.githubusercontent.com` (string equality, not substring/suffix
    match); redirects are not auto-followed — a 3xx response's `Location`
    is validated the same way and re-fetched manually, up to 3 hops, so a
    redirect can't be used to bypass the initial host check. Response body
    capped at 2000 chars (matching the existing per-release convention)
    before it reaches the LLM.

- **D-OUTPUT — Output shape unchanged.** The loop's final structured
  decision produces the same three fields `ReleaseDigest` already has
  (`migration_needed`, `migration_guide`, `breaking_changes`) — no new
  Pydantic fields on `ReleaseDigest` or `TargetInvestigation`.

- **D-FORMAT — `_format_targets` gains `breaking_changes`.** Fixes the
  pre-existing gap: the planner's prompt currently never sees
  `breaking_changes` even though it's computed. Added as another line in
  the per-target block, same style as the existing fields.

- **D-FALLBACK — Failure handling matches today's `classify_target`
  convention.** If the loop raises, or exhausts its iteration cap without a
  clean `finalize`, the node falls back to the same conservative default
  `classify_target`'s except-block used: `migration_needed=True`,
  `breaking_changes=["research failed, assuming breaking: <exc>"]`, empty
  `migration_guide`. Keeps the planner routing to a `codemod` task rather
  than silently treating a failed lookup as a clean bump.

- **D-ROLE — New `AgentRole.REMEDIATION_RELEASE_RESEARCH`; remove
  `AgentRole.REMEDIATION_CLASSIFY`.** The latter becomes unused once
  `classify.py` is deleted — removed rather than left dead, since
  `model_registry.py`'s `_validate_override_keys` already treats an unknown
  override key as a loud failure, and an enum member nothing resolves is
  the same kind of dead weight in the other direction.

## Out of scope

- Any change to the execution agent's own `read_release_notes` tool
  (`deepagent/tools.py`) — untouched by this spec.
- Any change to the analysis or discovery subgraphs (D-ENRICH).
- Fixing `fetch_release_notes`/`fetch_release_notes_between`'s pagination
  gap for any *other* caller — only `get_release_notes` (D-TOOLS) is
  paginated. `fetch_release_notes`/`fetch_release_notes_between` themselves
  are untouched (still used by `deepagent/tools.py`'s `read_release_notes`,
  out of scope per above).
- The 2000-char per-release/per-doc body truncation and the `per_page=100`
  page size — kept at their current/analogous values.
- A domain allowlist for `fetch_doc` instead of IP-range blocking —
  IP-range blocking was chosen so legitimate guides hosted anywhere aren't
  missed.
- Running research for r3 targets — r3's `replace` task doesn't consume
  `ReleaseDigest` content.
- Any repair of the discovery-side `dependency_graph.py` relocation beyond
  porting the one function (`dependents_of`) this spec needs (D-DEPENDENTS)
  — that module's broader completeness is a separate, pre-existing WIP.

## Success criteria

- `classify.py` and `test_classify.py` are deleted; nothing imports from
  `src.main_graph.subgraphs.remediation.classify`.
- `select_targets.py` exists with `select_targets_node` wired as the first
  node in `graph.py`; `select_remediation_targets` and `_has_no_upgrade`
  behave identically to their git-HEAD/pre-WIP versions (ported, not
  redesigned).
- `dependents_of` exists in `utils/dependency_graph.py`;
  `test_dependency_graph_helpers.py`'s `test_dependents_of_*` tests pass
  without modification (they were already written against the intended
  signature).
- `research_releases_node` exists, runs only for targets with
  `tier != "r3"`, and is wired into `graph.py` between `select_targets_node`
  and `build_migration_plan_node`.
- `get_release_notes` and `fetch_doc` tools exist; `fetch_doc` rejects a
  private/loopback/link-local/metadata-range URL, rejects a non-http(s)
  scheme, validates redirect targets the same way (up to 3 hops), and only
  attaches `GH_TOKEN` for `github.com`/`raw.githubusercontent.com` — each
  covered by a unit test.
- `get_release_notes` unit tests cover: a window fully covered by page 1
  (`has_more=False`, single `gh api` call), a window whose releases only
  appear on page 2 (`has_more=True` on page 1, agent's second call with
  `page=2` finds the window), and the page-10 hard cap being enforced
  regardless of `has_more`.
- A target whose release notes link to an external migration guide produces
  a non-empty `migration_guide` sourced from that guide's content in a test
  with mocked HTTP/container calls.
- `research_releases_node` failure (mocked exception) produces the same
  conservative fallback shape the old `classify_target` used, verified by a
  unit test.
- `_format_targets` includes `breaking_changes` in its output string;
  existing `plan.py` tests updated accordingly.
- `AgentRole.REMEDIATION_RELEASE_RESEARCH` is registered and resolvable via
  `get_role_llm`; `AgentRole.REMEDIATION_CLASSIFY` no longer exists;
  `test_model_registry.py` covers both.
- `test_remediation_subgraph.py`'s integration test is updated to mock
  `select_targets.select_remediation_targets`/`select_targets_node`'s
  dependencies instead of `classify.classify_target`, and still exercises
  the full `select → research → plan → remediate → verify` path.
- `apps/backend/docs/graphs.md`'s remediation section (mermaid diagram +
  node-by-node prose) is updated to describe `select_targets_node` and
  `research_releases_node` in place of `classify_targets_node`.
