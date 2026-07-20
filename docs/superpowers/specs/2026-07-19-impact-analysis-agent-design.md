# Impact Analysis Agent Design

**Date:** 2026-07-19
**Status:** Approved

## Overview

Replace `blast_radius`/`code_impact` as directly-exposed tools on
`finding_enricher` with a single, always-available `impact_analysis` tool
that wraps its own small nested ReAct agent. Today `blast_radius` is only
offered `if prep.codegraph_ready`, and even when available it only reports
counts (`affected_file_count`, `isolated_to_tests_or_scripts`) — the outer
enricher's LLM has to guess at what those files actually *do* from the
counts alone. `impact_analysis` makes graph-based analysis unconditionally
available (backed by a codegraph image whose presence is verified at process
startup, the same posture as MongoDB) and gives the analysis its own tool
access — `blast_radius`, a semantic usage-search fallback, and `read_file` —
so it can read the affected code and describe which business use cases are
actually impacted, not just how many files matched.

### Targeted problems

- **Conditional availability** — `blast_radius` silently disappears from a
  finding's tool map whenever `prep.codegraph_ready` is `False`, with no
  distinction between "codegraph isn't installed" (an infra problem) and
  "this specific repo failed to index" (recoverable via fallback). The
  outer enricher's LLM has no way to tell which happened; it just doesn't
  see the tool.
- **Shallow output** — `blast_radius` reports file counts and paths;
  `business_impact` is written by the outer LLM inferring from those counts
  alone, never having read the files themselves.
- **Two overlapping tools** — `blast_radius` (graph-based, precise) and
  `code_impact` (semantic, fuzzy) are both directly exposed to the outer
  loop's LLM today, which has to decide when to reach for the fallback
  itself. That decision belongs one level down, next to the data.

## Approach

**Two independent guarantees, not one.** "Codegraph should always be
available" bundles two different failure modes that need different fixes:

1. *The `codegraph-cli` image itself is missing or broken* — an
   infrastructure problem, identical in kind to MongoDB being unreachable.
   Fixed by a process-startup check that fails fast, so a broken deployment
   never silently degrades mid-job.
2. *This specific repository's `codegraph init` failed* (unusual repo
   structure, indexing timeout) — a per-job, data-dependent problem that a
   working image can still hit. Fixed by keeping the fallback path, but
   moving it inside `impact_analysis` where an LLM can decide from the
   actual tool output (`available: False`) rather than a `Python if`
   checked before any tool ran.

**Nested agent, not a bigger outer loop.** `finding_enricher`'s own ReAct
loop stays as-is (per
[[2026-07-18-report-subgraph-per-finding-agents-design]]) — it gains exactly
one new tool, `impact_analysis`, and loses two (`blast_radius`,
`code_impact`, no longer exposed directly). The complexity of "try the
graph, fall back to semantic search, read the files, decide what's
affected" is fully contained inside `impact_analysis`'s own small
bounded-iteration loop, invisible to the outer agent — the same reason the
report subgraph itself was restructured into isolated per-finding agents:
an agent with a narrower job and less context in view reasons more
reliably.

**`code_impact` is absorbed, not kept as a peer tool.** Its filtering logic
(query pre-seeded with `import X`/`require X`, results filtered to files
that actually contain the package name, lockfiles/manifests excluded,
snippet centered on the match) is real and worth keeping, but it becomes an
internal-only tool available solely to `impact_analysis`'s nested loop
(`find_usage_sites`), not a second tool the outer LLM has to choose between
manually.

## Components

### 1. Startup health check (`src/main.py`)

`src/main.py` currently has no `lifespan` hook at all — MongoDB
connectivity is discovered lazily on first DAO call, and the codegraph
image is never checked before job execution. This introduces the pattern
for both:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.db.connection import get_client
    from src.main_graph.adapters.docker_container_adapter import (
        DockerContainerAdapter,
    )

    await get_client().admin.command("ping")

    rc, _, stderr = await DockerContainerAdapter().run(
        image=settings.codegraph_docker_image, command="codegraph --version"
    )
    if rc != 0:
        raise RuntimeError(
            f"codegraph image '{settings.codegraph_docker_image}' is not "
            f"runnable (exit {rc}): {stderr}"
        )
    yield
```

`app = FastAPI(lifespan=lifespan)`. A failure in either check raises during
startup, which FastAPI/uvicorn surfaces as a failed boot — the process
never starts serving. This checks the *image*, not any specific repo's
index — per-repo indexing (`index_codegraph` in the discovery subgraph)
is unaffected and still runs per-job as it does today.

### 2. `impact_analysis_agent.py` (new)

`report/agents/impact_analysis_agent.py`. Exposes:

```python
def make_impact_analysis_tool(finding: FindingNote, prep: PrepResult, container):
    @tool
    async def impact_analysis(depth: int = 3) -> dict:
        """Investigate the real usage of this finding's package: which files
        import it, whether that reaches production code, and which business
        use cases/capabilities are actually affected. Always available."""
        return await analyze_impact(finding, prep, container, depth)
    return impact_analysis
```

`analyze_impact` runs its own bounded ReAct loop (own `_MAX_ITERATIONS = 3`,
same `Model.GPT_5_4_MINI`, own structured decision model
`ImpactAnalysisDecision(tool_calls, result: ImpactAnalysisResult | None,
finalize, reasoning)`) over three internal tools, none of which are ever
exposed to the outer `finding_enricher` loop:

- `blast_radius` — existing `make_blast_radius_tool`, unchanged.
- `find_usage_sites` — the relocated `code_impact.py` logic (same
  filtering: real source files only, package name must actually appear,
  snippet centered on the match), now private to this module.
- `read_file` — thin wrapper around `package_files.read_file`'s existing
  path-traversal-guarded implementation, scoped to `prep.repo_path`, letting
  the loop open specific affected files to judge what they actually do.

On finalize, the loop returns an `ImpactAnalysisResult`:

```python
class ImpactAnalysisResult(BaseModel):
    available: bool
    affected_file_count: int = 0
    affected_files: list[str] = Field(default_factory=list)
    production_file_count: int = 0
    isolated_to_tests_or_scripts: bool = False
    node_count: int = 0
    depth_searched: int = 0
    use_cases_impacted: list[str] = Field(default_factory=list)
    narrative: str = ""
    source: Literal["codegraph", "semantic_search", "unavailable"] = "unavailable"
```

`node_count`/`depth_searched` are graph-specific metadata: populated
directly from `blast_radius`'s own output when `source="codegraph"`, left
at `0` when `source` is `"semantic_search"` or `"unavailable"` (those paths
never call `blast_radius`, or it returned nothing usable).

The loop's own system prompt instructs it: call `blast_radius` first; if
`available: False`, fall back to `find_usage_sites`; read at least one
affected production file with `read_file` before writing `narrative` or
`use_cases_impacted` — never infer business impact from file paths alone.
Same tolerant error handling as `finding_enricher_agent`'s outer loop
(tool failures become error `ToolResult`s, loop continues; LLM-call
failures retry within budget; on budget exhaustion, returns whatever
`ImpactAnalysisResult` the last iteration produced, or an `available=False`
fallback if none did — this analysis never raises up into the outer loop).

### 3. `finding_enricher_agent.py` changes

- `_build_tool_map(finding, prep, container)` — signature gains `finding`
  (needed to construct `impact_analysis`, which is closed over the specific
  finding rather than taking `package_name` as an LLM-supplied argument).
  Drops the `if prep.codegraph_ready` conditional entirely and the
  `code_impact` entry; adds `"impact_analysis": make_impact_analysis_tool(finding, prep, container)`
  unconditionally.
- `_TOOL_DESCRIPTIONS` / `_SYSTEM`: `blast_radius`/`code_impact` entries
  replaced with one `impact_analysis` description. `business_impact`
  guidance now points at `impact_analysis`'s `narrative`/
  `use_cases_impacted` fields instead of raw counts.
- `_grounded_blast_radius` → renamed `_grounded_impact_analysis`, reads
  `tr.tool == "impact_analysis"`, builds the extended `BlastRadiusSummary`
  (see Schema) from the `ImpactAnalysisResult` fields, including
  `use_cases_impacted`/`narrative`/`source`.
- `impact_analysis`'s tool call carries no `package_name` argument (it's
  closed entirely over `finding`), so the existing `package_name`
  force-injection in `_run_tool` simply doesn't apply to it — no change
  needed there; it continues to apply to `web_search`.

### 4. `critique.py` changes

`_SYSTEM`'s business_impact-grounding line updates from "grounded in
blast_radius/code_impact output" to "grounded in impact_analysis output
(its narrative/use_cases_impacted, not invented)". No structural change —
`critique_report_finding` already receives the full `tool_results` list,
which now contains an `impact_analysis` entry instead of separate
`blast_radius`/`code_impact` entries.

### 5. Schema changes (`src/models/results.py`)

```python
class BlastRadiusSummary(BaseModel):
    available: bool
    affected_file_count: int = 0
    affected_files: list[str] = Field(default_factory=list)
    production_file_count: int = 0
    isolated_to_tests_or_scripts: bool = False
    node_count: int = 0
    depth_searched: int = 0
    use_cases_impacted: list[str] = Field(default_factory=list)
    narrative: str = ""
    source: Literal["codegraph", "semantic_search", "unavailable"] = "unavailable"


class ImpactAnalysisDecision(BaseModel):
    tool_calls: list[ToolCall]
    result: ImpactAnalysisResult | None
    finalize: bool
    reasoning: str
```

`ReportFinding.blast_radius: BlastRadiusSummary | None` keeps its existing
field name — deliberately not renamed, to avoid unnecessary churn in
`docs/backend/report.md` and any frontend code reading this shape; only its
contents get richer.

### 6. Deleted

`main_graph/tools/code_impact.py` and `tests/unit/tools/test_code_impact.py`
(confirmed only caller is `finding_enricher_agent.py`; its logic relocates
into `impact_analysis_agent.py`'s private `find_usage_sites`).

## Data Flow

```
process startup:
    lifespan(): ping MongoDB; run `codegraph --version` in codegraph_docker_image
        both must succeed, or the process fails to boot
        │
        ▼
finding_enricher (per finding, isolated — unchanged from existing design):
    tool_map = {web_search, impact_analysis}   # unconditional, no codegraph_ready check
    loop (bounded, outer):
        LLM decision: tool_calls | draft ReportFinding + finalize
        on impact_analysis(depth) call:
            │
            ▼
            analyze_impact (nested, bounded loop, own LLM):
                call blast_radius(dep_name, depth)
                    available: True  -> source="codegraph"
                    available: False -> call find_usage_sites(dep_name) -> source="semantic_search" (or "unavailable" if that also finds nothing)
                read_file(...) on >=1 affected production file
                finalize -> ImpactAnalysisResult{narrative, use_cases_impacted, ...}
            │
            ◀── ToolResult(tool="impact_analysis", output=ImpactAnalysisResult)
        on finalize: draft.blast_radius = _grounded_impact_analysis(tool_results)
        critique_report_finding(...) as today
```

## Error Handling

- `impact_analysis` never raises into the outer loop: internal tool
  failures become error `ToolResult`s (same pattern as the outer loop);
  internal LLM-call failures retry within the nested budget; if the nested
  budget is exhausted with no successful `ImpactAnalysisResult`, it returns
  `ImpactAnalysisResult(available=False, source="unavailable")` rather than
  propagating an exception.
- Startup check failure (Mongo unreachable or codegraph image unrunnable):
  process does not start. This is an intentional hard failure, not a
  degraded-mode fallback — same posture the codebase has never had for
  Mongo either, now made explicit for both.
- Per-repo `codegraph init` failure (discovery-time, unrelated to the
  image's health) is unaffected by this change and continues to surface as
  `blast_radius(...)` returning `available: False` at query time, which
  `analyze_impact` already treats as an ordinary fallback trigger, not an
  error.

## Testing

Unit (`tests/unit/`):
- `impact_analysis_agent`: `blast_radius` available → `source="codegraph"`,
  narrative uses `read_file` output; `blast_radius` unavailable →
  `find_usage_sites` fallback used, `source="semantic_search"`; both
  unavailable → `ImpactAnalysisResult(available=False, source="unavailable")`,
  no exception; internal budget exhaustion → returns last-known
  `ImpactAnalysisResult` or the `unavailable` fallback, never raises.
- `finding_enricher_agent`: `_build_tool_map` includes `impact_analysis`
  regardless of `prep.codegraph_ready` (replaces the old
  codegraph_ready-gating test); `_grounded_impact_analysis` builds
  `BlastRadiusSummary` with `use_cases_impacted`/`narrative`/`source` from
  a mocked `impact_analysis` tool result.
- `main.py` lifespan: Mongo ping failure and codegraph version-check
  failure each raise before yield (mock `get_client`/`DockerContainerAdapter`).

Subgraph (`tests/subgraphs/test_report_subgraph.py`):
- Existing `test_report_grounds_blast_radius_via_codegraph` updated: mocks
  `impact_analysis_agent.analyze_impact` (or the underlying container call)
  instead of gating on `codegraph_ready`; asserts `finding.blast_radius`
  carries `narrative`/`use_cases_impacted` alongside the existing count
  fields.

## Non-goals

- **Changing per-repo `codegraph init` / discovery-time indexing** — out of
  scope; `index_codegraph`, `PrepResult.codegraph_ready`, and the discovery
  subgraph are untouched. This design only removes `codegraph_ready` as a
  *tool-availability* gate in the report subgraph.
- **Renaming `BlastRadiusSummary`/`ReportFinding.blast_radius`** —
  considered and rejected; the field grows richer contents, not a new name.
- **A generic startup-health-check framework** (registry of arbitrary
  checks, retry/backoff policies) — two concrete checks (Mongo, codegraph)
  inline in `lifespan`, not a reusable abstraction. YAGNI until a third
  check is needed.
- **Auto-building/pulling the codegraph image at startup** — the startup
  check verifies the image is already present and runnable; it does not
  attempt to build or pull it. Building remains the existing manual
  `make docker-build-codegraph` step.

## Summary of Changes

| File | Change |
|------|--------|
| `src/main.py` | New `lifespan` hook: ping MongoDB, verify codegraph image runnable; fail startup otherwise |
| `report/agents/impact_analysis_agent.py` | New: nested bounded ReAct agent (`blast_radius`, `find_usage_sites`, `read_file`) producing `ImpactAnalysisResult` |
| `report/agents/finding_enricher_agent.py` | `_build_tool_map` gains `finding` param, drops `codegraph_ready` gate and `code_impact`, adds unconditional `impact_analysis`; `_grounded_blast_radius` → `_grounded_impact_analysis`; prompt updated |
| `report/agents/critique.py` | System prompt references `impact_analysis` instead of `blast_radius`/`code_impact` |
| `src/models/results.py` | `BlastRadiusSummary` gains `use_cases_impacted`/`narrative`/`source`; new `ImpactAnalysisResult`, `ImpactAnalysisDecision` |
| `main_graph/tools/code_impact.py` | Deleted; logic relocated into `impact_analysis_agent.py`'s private `find_usage_sites` |
| `docs/backend/report.md` | Updated for extended `blast_radius` shape |
| `tests/unit/`, `tests/subgraphs/` | Coverage per Testing section |
