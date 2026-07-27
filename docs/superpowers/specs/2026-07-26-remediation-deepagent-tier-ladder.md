# Spec: Remediation Subgraph — Deepagent Tier Ladder

**Date:** 2026-07-26
**Scope:** Backend only (`apps/backend`), limited to the internals of
`remediation_subgraph` (`src/main_graph/subgraphs/remediation/`). Discovery,
analysis, and report subgraphs are untouched. Frontend rendering of the new
remediation execution graph is out of scope (mirrors the equivalent deferral
in the analysis-subgraph swap).

## Context

`docs/superpowers/specs/2026-07-25-remediation-tier0-1-design.md` shipped the
first rung of `docs/superpowers/roadmap.md` Workstream C: a deterministic
target-selection step feeding a single-shot structured-output orchestrator
loop (`run_remediation`) that proposes same-package version bumps, verifies
them jointly in the sandbox, and opens one PR per job via `gh`. It
deliberately scoped out Tier 2 (code-adapting bumps) and Tier 3 (package
replacement) as separate future specs, and its own "Out of scope" note on the
analysis-subgraph deepagent swap (`2026-07-26-analysis-subgraph-deepagent-swap.md`)
explicitly held the line that "the reliability roadmap's argument against
agentic tool freedom in remediation still holds regardless of this spec's
outcome."

This spec is that reconsideration, made deliberately rather than by drift.
The trigger: after using `deepagents` successfully and narrowly on the
analysis subgraph (verified against the real library, not assumed), the user
proposed extending the same technology to remediation with a materially
larger job than a same-package bump — reviewing a dependency's release notes
against this project's actual usage, deciding whether a bump is safe,
requires code adaptation, or requires replacing the package outright, and
opening a PR either way. That decision (bump vs. adapt vs. replace) is
precisely the kind of judgment call a fixed, single-shot structured-output
call cannot make well, but a tool-using, self-correcting agent can — provided
the roadmap's non-negotiable guardrail carries forward unchanged:
**verification is the spine, and nothing ships unverified.** This spec's job
is to design the agentic decision-making without weakening that guardrail,
not to relax it.

## Problem being preserved, not just the framework swap

Three invariants from the tier0/1 spec must not regress:

1. **Joint verification.** Whatever lands in a PR must verify *together* —
   a change is never shipped because it looked fine in isolation while the
   full set regressed something else.
2. **Consent gating.** `remediate=false` still runs the full stage (produces
   `Remediation` records and patches) but opens zero branches, zero PRs,
   makes zero `gh`/`git` calls. Writing to a repo never happens silently.
3. **Honest partial success.** A target that can't be resolved ships as
   `failed`/`skipped` with a reason; it never blocks or silently drops
   everything else that did verify.

Two new invariants this spec adds, driven directly by the design discussion:

4. **Cross-target coupling is discovered, not assumed.** Fixing one
   dependency can require also touching another dependency that was never
   independently flagged as a finding (e.g. bumping `eslint` requiring a
   compatible `eslint-plugin-*`). The system must be able to notice this
   mid-remediation and act on it, not just fail the sandbox gate and give up.
5. **A coupled fix ships as one atomic unit, or not at all.** If dependency B
   is only in scope because dependency A's remediation required it, A and B's
   changes verify and ship together — never a PR with half a coupled change.

## Decisions

- **D1 — Swap boundary & what's reused.** Replaced:
  `orchestrator.py::run_remediation` and its `RemediationDecision`
  single-shot decider. Extended: `selection.py::select_remediation_targets`
  keeps its exact deterministic logic (severity filter, transitive-to-direct
  anchoring, unification) but now seeds the *initial* target set, not the
  final one — the agent layer can grow it via discovered coupling (D8).
  Reused as-is: `verify.py`'s install/build/test/audit logic (repackaged as
  an agent tool and as the deterministic backstop, not removed), `workspace.py`
  (`copy_repo`, `apply_bump`, `working_copy_diff`), `gh_cli_adapter.py`/
  `git_pr_port.py` (invoked per-group instead of per-job). The subgraph's
  external contract with the main graph is unchanged: in `{job_id, concern,
  prep_result_id, analysis_result_id}`, out `{remediation_result_id}`.
- **D2 — Root deepagent dispatches per-target work via `task()`; the root's
  own tool surface is dispatch-only.** One root `create_deep_agent(...)` per
  job. Its only tool is `task()` against a single `CompiledSubAgent` spec
  ("remediate one target"), mirroring D3 of the analysis-subgraph swap (keep
  the root's own tool surface minimal — no direct file/shell access at the
  root). All the real work — reading release notes, checking usage, deciding
  bump vs. adapt vs. replace, editing files, verifying — happens inside the
  per-target subagent, not the root.
- **D3 — No raw shell/`execute` tool surface, anywhere.** Same deliberate
  deviation as D3 of the analysis-subgraph swap, extended to remediation's
  larger tool needs. The per-target subagent's tools are a fixed, named set:
  `read_release_notes` (wraps `gh api`/`gh release view` — reuses the `gh`
  CLI already used for PR creation), `blast_radius` (existing container-sandboxed
  CodeGraph tool, unchanged), `search_code` (existing per-job vector store
  tool, unchanged), `dependents_of` (new — see D8), file read/write/edit
  (via `FilesystemBackend`, scoped to that target's own clone — see D4),
  `bump_dependency` (wraps `workspace.apply_bump`), and `verify` (wraps
  `verify.verify_working_copy`, informative only — see D7). No general-purpose
  shell execution is ever reachable by any agent in this subgraph.
- **D4 — Isolated per-target working copies; the shared moment is
  group-verify, not a shared directory.** Each per-target subagent's
  `FilesystemBackend(root_dir=...)` points at its *own* clone
  (`workspace.copy_repo(prep.repo_path)`, called once per target). This was a
  correction made during design: a single shared working copy would let two
  concurrently-dispatched target subagents (the root can call `task()` more
  than once per turn) corrupt each other's state via concurrent installs/file
  writes — the exact class of hazard the analysis-subgraph swap hit with
  parallel `task()` calls writing to the same state channel, here manifesting
  on disk instead of in LangGraph state. Isolation removes the hazard without
  losing coupling detection, because coupling is discovered through the
  `requires` signal (D8), not through one subagent observing another's live
  edits. The genuinely joint step is `group_and_verify_gate` (D6): a
  connected group's member patches are replayed together onto one clean
  clone and verified as a unit before anything ships.
- **D5 — Structured output + reducer-based state merge, reusing the pattern
  (and its bugfix) from the analysis-subgraph swap.** The per-target
  `CompiledSubAgent`'s `runnable` is a thin wrapper around a *nested*
  `create_deep_agent(tools=[...], backend=FilesystemBackend(root_dir=clone),
  response_format=RemediationOutcome)`. `RemediationOutcome` is a compact
  model: `strategy`, `to_range` / `replacement_dep` / `replacement_range`,
  `migration_plan`, `requires: list[str]`. The wrapper invokes the nested
  agent, reads its `structured_response` directly (confirmed against the real
  library that `structured_response` is *excluded* from deepagents' own
  subagent state passthrough — see "Verified against the real library"), and
  returns `Command(update={"remediations": [...], "requires_edges": [...],
  ...})` merging into the root's state via ordinary reducers, the same
  mechanism D4 of the analysis-subgraph swap established. The outer
  `RemediationState` node diffs incoming `remediations`/`requires_edges` by
  identity (`Remediation.id`, edge identity) each time the root agent
  returns, not positionally — this is the exact fix the analysis-subgraph
  swap needed after a real double-counting bug on a second correction round,
  applied proactively here instead of rediscovered. The same swap's *other*
  real bug — plain, un-annotated state fields crashing when two `task()`
  calls land in the same superstep — is guarded against the same way here:
  `targets` (the dep-name-keyed lookup table the wrapper resolves a
  dispatched target from) is seeded once by `root_deepagent_node` before the
  agent run starts and never written to again by any tool, but still uses a
  `_keep_first`-style reducer (`Annotated[dict[str, dict], _keep_first]`),
  not a bare `dict`, precisely because this design explicitly allows the
  root to dispatch more than one target per turn (D2) — a bare, un-annotated
  field is unsafe the moment concurrent writes are structurally possible,
  regardless of whether today's logic happens to only ever write it once.
  Unlike D4's disk isolation this is a LangGraph state-channel concern, not a
  filesystem one, and needs its own fix applied from the start rather than
  discovered via a production crash.
- **D6 — Group verification is the deterministic backstop; a subagent's own
  "looks green" is never authoritative.** After the root agent considers its
  work done (or hits its bound), a deterministic `group_and_verify_gate`
  computes connected groups from `requires_edges` (D8), and for each group:
  replays its members' bumps (declaratively, via `apply_bump` — never a raw
  patch for `package.json`, to avoid manifest merge conflicts) and code
  changes (via `git apply` of each member's `code_diff`) onto one clean clone
  from the base ref, then re-runs the full install/build/test/audit
  verification from scratch. Only this result decides `status`. If a group
  fails, the failure is fed back to the root (which group, why) for a bounded
  number of correction rounds (mirrors the analysis-subgraph swap's
  `correction_rounds` field and cap), reusing today's orchestrator's existing
  "not green: feed the failure back into the loop, re-plan" invariant instead
  of discarding it.
- **D7 — The tier decision (bump / adapt / replace) is agent judgment,
  grounded in tool evidence, never hardcoded.** The subagent's prompt
  directs it to read release notes across the current→candidate range
  (`read_release_notes`), check real usage (`blast_radius`, `search_code`),
  and reason: no relevant breaking changes → bump-only; breaking changes with
  adaptable call sites → bump + edit the affected files itself; evidence
  (including the finding's own `description` text) suggests the package
  should be replaced entirely → propose a replacement and migrate usage. It
  may call `verify` itself, repeatedly, to self-correct before finalizing —
  this is the "agent-driven verification" the user explicitly chose over a
  graph-micromanaged loop — but D6's independent re-verification is what
  actually sets `status`, never the agent's own claim.
- **D8 — Coupling and non-finding impact both flow through one mechanism:
  `requires`.** A subagent's `RemediationOutcome.requires: list[str]` names
  any dependency (finding-anchored or not) that must move together with its
  own target for the fix to be coherent — whether because release notes say
  so, or because a new `dependents_of(graph, name)` tool (a small
  generalization of `dependency_graph.py::direct_dependents`'s existing
  parent-walk, now returning *every* package that depends on `name`, not
  only direct-dependency roots) surfaced a package the subagent decided to
  investigate. When the root sees an unresolved `requires` entry, it
  dispatches a new target for it — same mechanism, same tools, regardless of
  whether that dependency has an associated `FindingNote`. `Remediation`
  gains a `required_by: list[str]` field so a companion-only change (no
  `addresses`) still carries an honest, human-readable reason in the PR body.
- **D9 — One PR per connected group, consent-gated, capped.** A connected
  group (a target plus everything pulled in transitively via `requires`) gets
  exactly one PR — an uncoupled target is a group of one, matching the tier0/1
  spec's original per-job case as the common special case. This was an
  explicit choice over one PR per job or one PR per raw target: reviewers can
  approve/merge a safe, isolated bump independently of a riskier coupled or
  Tier-2/3 change, and PR bundling is driven only by what the dependency
  graph actually forced, never by convenience. `remediate=false` still
  computes every group but opens zero PRs, uniformly.
- **D10 — Three independent bounds guard three independent runaway risks.**
  The root deep agent's own `recursion_limit` (agent takes too many turns);
  a correction-round cap on `group_and_verify_gate` (a group's verification
  keeps failing); and a cap on total distinct targets/groups processed (a
  `requires` chain that never stabilizes). Each bound fails honestly into
  `skipped`/`failed` with a reason on whatever didn't finish — never an
  unbounded loop, never a silent drop.

## Data flow

```
START
  -> select_remediation_targets (unchanged, deterministic)
       - severity filter, transitive-to-direct anchoring, unification
       - seeds the INITIAL target set only
  -> root_deepagent_node
       - root deep agent's only tool is task() against the
         "remediate_target" CompiledSubAgent
       - dispatches one task() per open target; each subagent:
           read_release_notes / blast_radius / search_code / dependents_of
           -> decide bump | bump+adapt | replace
           -> edit files on its OWN isolated clone (D4)
           -> call verify itself, iterate until it's satisfied or gives up
           -> finalize with RemediationOutcome (structured_response)
       - wrapper extracts structured_response, returns Command(update=...)
         merging into RemediationDeepAgentState via reducers (D5)
       - a subagent's requires entries the root hasn't seen yet -> dispatch
         a new target for them, same mechanism
       - bounded by recursion_limit and the target/group cap (D10)
  -> group_and_verify_gate
       - compute connected groups from requires_edges
       - per group: replay bumps (apply_bump) + code diffs (git apply) onto
         a clean clone, re-run full verify from scratch (D6)
       - group verified green -> status=fixed
       - group failed, correction_rounds < cap -> feed back to
         root_deepagent_node (which group, why), loop
       - group failed, cap exhausted -> status=failed, ships honestly
  -> pr_and_persist_node
       - per fixed group, if consent: branch + commit + push + gh pr create
         (D9); if not consent: pr_url=None, patches still recorded
       - persist RemediationResult (aggregates all Remediation records)
  -> END
```

## Out of scope

- Any change to `discovery`, `analysis`, or `report` subgraphs.
- Frontend rendering of the new remediation execution graph — mirrors the
  analysis-subgraph swap's equivalent, explicit deferral.
- A proper `create_pull` port/adapter (roadmap Workstream D2). Still `gh`
  CLI, same as the tier0/1 spec's original non-goal.
- New severity configuration — still `settings.risk_min_severity`, unchanged.
- Any claim that Tier 2/3 (adapt, replace) reach the same reliability as
  Tier 0/1 (bump). The roadmap is explicit that code migration and package
  replacement are a research frontier bounded by the target repo's own test
  quality — this spec builds the mechanism (propose, adapt or replace,
  verify, PR) honestly, not a reliability guarantee. A repo with poor test
  coverage will get an honestly-reported `built=True, tested=None` outcome
  it can inspect, not a false "it works."
- Real-world validation of the tier *decision* itself (does the agent
  actually choose correctly between bump/adapt/replace on real release
  notes) — unit tests prove the mechanism acts correctly on whatever the
  model decides (via scripted fake models), not that the model decides well.
  First live validation is a manual run, same as tier0/1.

## Verified against the real library

Confirmed by inspecting the installed `deepagents` package directly (not
docs alone) — same discipline as the analysis-subgraph swap:

- `deepagents.backends.filesystem.FilesystemBackend(root_dir: str | Path |
  None = None)` exists and scopes all of an agent's built-in file tools
  (`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`) to a real
  directory on disk — relative paths resolve under `root_dir`, and resolved
  paths are verified to stay within it. This is what makes D4's per-target
  real-disk isolation possible without hand-rolling file tools.
- `create_deep_agent(..., backend: BackendProtocol | BackendFactory | None =
  None, response_format: ResponseFormat[ResponseT] | type[ResponseT] | dict
  | None = None, ...)` — both are first-class, documented parameters on the
  same function used for the root agent in the analysis-subgraph swap.
- Prompt-based subagents (unlike `CompiledSubAgent`) can declare their own
  `response_format` in their spec (`middleware/subagents.py`), producing a
  `structured_response` — but that key is explicitly listed in
  `_EXCLUDED_STATE_KEYS` and is **not** auto-merged into the dispatching
  agent's state by deepagents itself. This confirms D5's design: the nested
  deep agent (inside the `CompiledSubAgent` wrapper) is the one that gets
  `response_format`, and the wrapper's own code — not deepagents' plumbing —
  is responsible for reading `structured_response` off the nested agent's
  result and turning it into the `Command(update=...)` that reaches the
  root's reducers. This mirrors exactly how D4 of the analysis-subgraph swap
  already established: state reaches the root through the wrapper's own
  return value, not through any deepagents auto-merge of subagent-internal
  fields.
- `execute` (deepagents' built-in shell tool) only activates for backends
  implementing `SandboxBackendProtocol`; `FilesystemBackend` does not
  implement it, and it is never added to any agent's `tools=[...]` list in
  this design regardless — confirms D3 holds structurally, not just by
  omission from the prompt.

## Success criteria

- Tier 0/1 behavior (pure same-package bump, no coupling) verifies and ships
  at least as reliably as the current merged implementation — this spec must
  not regress the tier0/1 spec's shipped behavior for the common case.
- A dependency requiring a companion bump neither flagged as a finding nor
  originally selected as a target (the `eslint`/`eslint-plugin-*` scenario)
  gets pulled in via `requires`, ships in the same group's PR, and carries a
  human-readable `required_by` reason.
- A coupled group never partially ships: `group_and_verify_gate`'s replay+
  verify is atomic per group.
- Exactly one PR per connected group, correctly tier-labeled, and
  `remediate=false` opens zero PRs across every group in the job.
- No raw shell/`execute` tool is reachable by the root agent or any subagent.
- Full backend suite, ruff, and mypy green. `dependents_of` and the
  connected-group computation are pure, deterministic, and unit-tested with
  no LLM dependency, matching the style of today's `selection.py` tests.
  Root+subagent integration is tested with real `deepagents` machinery and
  scripted fake chat models (same pattern as the analysis-subgraph swap),
  explicitly covering: a `requires` signal causing a new dispatch, parallel
  `task()` calls not corrupting root state, and the correction-round loop
  terminating at its cap.
