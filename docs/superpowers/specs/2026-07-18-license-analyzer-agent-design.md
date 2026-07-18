# License Analyzer Agent — Design

## Motivation

The analysis pipeline currently has no agent that reasons about license
compatibility across the dependency tree. There is an existing
`check_licenses` tool (`apps/backend/src/main_graph/tools/package_files.py`)
but it only reads `node_modules`, and `node_modules` is not present on disk
for the common case where the cloned repo already ships a lock file (see
"Why `check_licenses` doesn't work" below). It also only checks each
package's license name against a fixed permissive allow-list — it cannot
detect actual *incompatibility* between a project's own license and its
dependencies', or copyleft contagion through the dependency tree.

This design is informed by Liu et al., "Catch the Butterfly: Peeking into
the Terms and Conflicts among SPDX Licenses" (arXiv:2401.10636), which
models each SPDX license as a set of rights/obligations terms with CAN /
CANNOT / MUST attitudes, and defines three conflict types between a
Project License (PL) and a Component License (CL):

- **C1** — the project's license grants a *right* the dependency's license
  does not grant (e.g. project permits sublicensing, dependency forbids it).
- **C2** — the dependency's license requires an *obligation* the project
  does not fulfill (e.g. dependency requires "include notice", project
  doesn't declare it).
- **C3** — copyleft contagion: a copyleft dependency requires all of its
  granted rights to propagate into derivative works, and the project's
  license fails to preserve them.

Replicating the paper's full term model (22 terms, CAN/CANNOT/MUST, across
all 453 SPDX licenses via NLP term extraction) is out of scope for this
agent. Instead we approximate it with a curated knowledge base covering the
licenses actually seen in the npm ecosystem, and apply simplified C1/C2/C3
rules against that table. Licenses outside the curated table are flagged
for manual review rather than guessed.

## Why `check_licenses` doesn't work today

Traced via the discovery subgraph
(`apps/backend/src/main_graph/subgraphs/discovery/graph.py`):
`clone_repo → inspect_repo → [install_deps, conditional] → index_repository
→ build_project_context → save_prep_result`. `install_deps` (the only step
that runs `npm/yarn/pnpm install` and therefore creates `node_modules`) is
routed to only when `inspect_repo` sets `has_lock_file=False`. For any repo
that already ships a lock file — the common case for real projects —
`install_deps` never runs, so `node_modules` never exists on disk, and
`check_licenses`'s `os.listdir(node_modules)` finds nothing.

`npm`'s lock file, however, already carries per-package license data
without needing an install: `package-lock.json`'s `"packages"` entries
include a `"license"` field mirrored from each package's own
`package.json` (confirmed empirically: 179/377 packages had the field in a
sample lockfile in this repo's fixtures). `yarn.lock` / `pnpm-lock.yaml` do
not carry this metadata, so those package managers need a registry
fallback (see below).

## Architecture

- New agent class `LicenseAgent` in
  `apps/backend/src/main_graph/subgraphs/analysis/agents/license_agent.py`,
  registered as `"license_agent"` in
  `apps/backend/src/main_graph/subgraphs/analysis/agents/registry.py`.
- Follows the `VulnerabilityAgent` pattern (deterministic, not the LLM
  ReAct loop used by `SupplyChainAgent`/`MaintenanceAgent`): license
  compatibility is a rule computation over the whole tree, not an
  exploratory investigation, and legal-risk findings should not depend on
  an LLM's compatibility judgment. `run()` is overridden directly;
  `packages_to_focus` is ignored (a single copyleft transitive dependency
  matters regardless of which packages the conductor asked about).
- `analysis_conductor.py`'s system prompt gets one more dispatch-strategy
  line, mirroring the existing `vulnerability_agent` guidance: dispatch
  `license_agent` at most once, `packages_to_focus` empty, never sharded.
- No other graph/state changes — the conductor already discovers agents
  purely from `get_agent_descriptions()` (`registry.py`), and
  `PrepResult` (already containing `repo_path`, `detected_package_manager`,
  `dependency_graph`) has everything the agent needs.

## License collection

New module
`apps/backend/src/main_graph/subgraphs/analysis/agents/license_collector.py`,
exposing `async def collect_licenses(prep: PrepResult) -> dict[str, str]`
(package key `name@version` → raw license string):

1. **npm**: parse `package-lock.json` directly (not `node_modules`) and
   read each entry's `"license"` field.
2. **yarn/pnpm**, or any npm entry missing the field: fall back to the npm
   registry packument via `_npm_metadata` (reused from
   `apps/backend/src/main_graph/tools/external_api.py`, already cached
   per-process), fetched concurrently under a semaphore (cap concurrency,
   not coverage — every package in `prep.dependency_graph["packages"]` is
   still checked, just not all at once) to avoid hammering the registry on
   large trees.
3. A package with no resolvable license after both steps is recorded as
   `"UNKNOWN"` — surfaced as its own low-severity finding, never guessed.

## Curated license knowledge base

New module
`apps/backend/src/main_graph/subgraphs/analysis/agents/license_data.py`: a
hand-curated table of the SPDX ids common in the npm ecosystem (MIT, ISC,
Apache-2.0, BSD-2-Clause, BSD-3-Clause, 0BSD, Unlicense, CC0-1.0, MPL-2.0,
LGPL-2.1-only/or-later, LGPL-3.0-only/or-later, GPL-2.0-only/or-later,
GPL-3.0-only/or-later, AGPL-3.0-only/or-later, and a few more as needed).
Each entry has:

- `category`: `permissive` | `weak_copyleft` | `strong_copyleft` |
  `network_copyleft` (AGPL) | `public_domain`
- rights/obligations relevant to conflict detection: `sublicense`,
  `commercial_use` (CAN/CANNOT), `include_notice`, `disclose_source`,
  `state_changes`, `same_license` (MUST/not-required)

**Normalization** before lookup: exact SPDX id match, plus simple `"A OR
B"` (satisfied if either side is in the curated table and compatible) and
`"A AND B"` (both sides must be satisfiable) expressions. Anything more
complex — custom license text, `SEE LICENSE IN <file>`, nested/parenthesized
expressions, or an id not in the curated table — resolves to `unknown` and
is surfaced as a manual-review finding rather than approximated.

## Conflict rule engine

New module
`apps/backend/src/main_graph/subgraphs/analysis/agents/license_rules.py`,
pure functions over `(project_license_row, dependency_license_row)`
implementing simplified C1/C2/C3:

- **C1** (rights conflict): project's license CAN something the
  dependency's license marks CANNOT (e.g. project allows sublicensing,
  dependency forbids it) → `medium` severity.
- **C2** (obligation gap): dependency's license MUSTs an obligation
  (`include_notice`, `disclose_source`, `state_changes`) the project
  doesn't fulfill → `low` severity (informational — an obligation to
  satisfy, not necessarily a violation yet).
- **C3** (copyleft contagion): dependency's `same_license` obligation is
  MUST (true for `strong_copyleft` and `network_copyleft` categories — the
  two are treated identically for C3 purposes, since the paper's
  "network use is distribution" term collapses into the same propagation
  requirement) and the project's license is not the same license or a
  category the dependency's license permits as compatible → `high`
  severity (the paper's most consequential case — e.g. a GPL-3.0
  dependency pulled into an MIT-licensed project).

**Project License (PL)** = root `package.json`'s `"license"` field. Missing
or `"UNLICENSED"` is treated as proprietary/all-rights-reserved (the most
restrictive stance), which will legitimately surface C1/C2 findings against
most dependencies requiring attribution — expected behavior, not a bug.

## Output shape

Each conflict becomes a `FindingNote` (existing model in
`src/models/conductor.py`): `dep_name`, `severity` (per C1/C2/C3 mapping
above), `description` naming the specific term conflict and both licenses
involved, `evidence` citing the source (`lockfile` field or npm registry
lookup). This mirrors `audit_parser.py`'s `parse_audit_findings` shape and
role exactly, substituting license data for audit output. `UNKNOWN`
licenses get their own `info`-severity finding per affected package.

The existing `check_licenses` tool is superseded by this agent and removed
(it is not registered on any current agent's toolkit, so nothing else
depends on it).

## Testing

Unit tests (pytest, following existing conventions in
`apps/backend/tests/`):

- npm lockfile license extraction (including packages missing the field).
- yarn/pnpm registry-fallback path (mocked `_npm_metadata`).
- SPDX `OR`/`AND` expression normalization, including cases that fall back
  to `unknown`.
- C1/C2/C3 rule engine against known pairs, e.g.: GPL-3.0-only dependency +
  MIT project → C3/high; MIT dependency (requires `include_notice`) + no
  project license declared → C2/low; project license CAN sublicense +
  Apache-2.0 dependency (CAN sublicense) → no conflict.
- `LicenseAgent.run()` end-to-end against a fixture `PrepResult` +
  dependency graph, asserting the produced `FindingNote` list and that
  `packages_to_focus` is ignored.
