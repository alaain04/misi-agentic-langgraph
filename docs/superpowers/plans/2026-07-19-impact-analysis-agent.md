# Impact Analysis Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make blast-radius analysis unconditionally available to every finding (backed by a process-startup health check on the codegraph image, mirroring how MongoDB unreachability should already—but doesn't yet—block startup), and make it richer: wrap the raw `blast_radius` tool in a small nested ReAct agent (`impact_analysis`) that also reads affected files and a semantic-search fallback, so it can describe which business use cases are actually impacted instead of just reporting file counts.

**Architecture:** `finding_enricher`'s outer ReAct loop (unchanged in shape) gains one new always-present tool, `impact_analysis`, replacing the conditionally-available `blast_radius`/`code_impact` pair. Internally, `impact_analysis` runs its own bounded ReAct loop (`report/agents/impact_analysis_agent.py`) over `blast_radius`, a relocated `find_usage_sites` (absorbing `code_impact.py`'s logic), and `read_file`. Numeric/path fields are grounded deterministically from actual tool output (never LLM-authored, to prevent hallucinated counts); only `narrative`/`use_cases_impacted` come from the nested LLM. A new FastAPI `lifespan` hook in `src/main.py` verifies MongoDB and the codegraph image at process boot, failing fast if either is broken.

**Tech Stack:** Python 3.12+, LangGraph/LangChain `@tool`, Pydantic v2, FastAPI `lifespan`, `uv`/`pytest`/`pytest-asyncio`, ruff, mypy.

## Global Constraints

- All backend commands run from `apps/backend/`: `cd apps/backend && uv run <cmd>`.
- `BlastRadiusSummary`/`ReportFinding.blast_radius` keep their existing names — not renamed, only extended with `use_cases_impacted: list[str]`, `narrative: str`, `source: Literal["codegraph","semantic_search","unavailable"]`.
- Numeric/path fields on the final `BlastRadiusSummary` (`affected_file_count`, `affected_files`, `production_file_count`, `isolated_to_tests_or_scripts`, `node_count`, `depth_searched`, `source`, `available`) are always grounded deterministically from actual tool output inside `analyze_impact` — the nested LLM only ever authors `narrative`/`use_cases_impacted`. This is the anti-hallucination guarantee this feature exists to strengthen.
- `impact_analysis` is unconditionally in `finding_enricher`'s tool map — no `if prep.codegraph_ready` check anywhere in the report subgraph after this plan. Per-repo indexing failure (`codegraph init` failing for a specific repo) is still handled, but as an ordinary `available: False` result from `blast_radius` that `analyze_impact` falls back on — not a Python-level conditional.
- Reference spec: `docs/superpowers/specs/2026-07-19-impact-analysis-agent-design.md`.
- Two model simplifications made while writing this plan, both consistent with the spec's intent (not scope changes): (1) the spec's standalone `ImpactAnalysisResult` model is dropped — `analyze_impact` returns `BlastRadiusSummary` directly, since the two were identical in shape; (2) `ImpactAnalysisDecision.result: ImpactAnalysisResult | None` becomes `narrative: str` + `use_cases_impacted: list[str]` directly on the decision, since those are the only fields the nested LLM is ever allowed to author.

---

### Task 1: Schema changes — extend `BlastRadiusSummary`, add `ImpactAnalysisDecision`

**Files:**
- Modify: `apps/backend/src/models/results.py`
- Test: `apps/backend/tests/unit/test_result_models.py`

**Interfaces:**
- Produces: `BlastRadiusSummary.use_cases_impacted: list[str]`, `.narrative: str`, `.source: Literal["codegraph","semantic_search","unavailable"]` (all with defaults, so existing callers/tests constructing `BlastRadiusSummary(available=True, ...)` without them keep working); `ImpactAnalysisDecision(tool_calls: list[ToolCall], narrative: str, use_cases_impacted: list[str], finalize: bool, reasoning: str)`. Consumed by Task 2 (`impact_analysis_agent.py`).

- [ ] **Step 1: Write the failing tests**

In `apps/backend/tests/unit/test_result_models.py`, replace the import block at the top of the file:

```python
from src.models.conductor import FindingNote
from src.models.results import (
    AgentCallRecord,
    AgentDispatch,
    AnalysisConductorDecision,
    DomainAgentDecision,
    EvidenceBundle,
    FindingEnrichmentDecision,
    PrepResult,
    ReportFinding,
    ReportResult,
)
```

with:

```python
from src.models.conductor import FindingNote
from src.models.results import (
    AgentCallRecord,
    AgentDispatch,
    AnalysisConductorDecision,
    BlastRadiusSummary,
    DomainAgentDecision,
    EvidenceBundle,
    FindingEnrichmentDecision,
    ImpactAnalysisDecision,
    PrepResult,
    ReportFinding,
    ReportResult,
)
```

Then append to the bottom of the file:

```python
def test_blast_radius_summary_defaults_narrative_fields_empty():
    s = BlastRadiusSummary(available=False)
    assert s.use_cases_impacted == []
    assert s.narrative == ""
    assert s.source == "unavailable"


def test_blast_radius_summary_accepts_narrative_fields():
    s = BlastRadiusSummary(
        available=True,
        affected_file_count=2,
        use_cases_impacted=["checkout flow"],
        narrative="Used to format currency in checkout.",
        source="codegraph",
    )
    assert s.use_cases_impacted == ["checkout flow"]
    assert s.source == "codegraph"


def test_impact_analysis_decision_round_trip():
    d = ImpactAnalysisDecision(
        tool_calls=[],
        narrative="",
        use_cases_impacted=[],
        finalize=False,
        reasoning="need more evidence",
    )
    assert d.finalize is False
    assert d.narrative == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_result_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'ImpactAnalysisDecision'`

- [ ] **Step 3: Implement the schema changes**

In `apps/backend/src/models/results.py`, add `Literal` to the typing import — the file currently starts with:

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from src.models.conductor import FindingNote, ToolCall
```

Change to:

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.models.conductor import FindingNote, ToolCall
```

Replace:

```python
class BlastRadiusSummary(BaseModel):
    available: bool
    affected_file_count: int = 0
    affected_files: list[str] = Field(default_factory=list)
    production_file_count: int = 0
    isolated_to_tests_or_scripts: bool = False
    node_count: int = 0
    depth_searched: int = 0
```

with:

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
```

Add a new class directly after `FindingEnrichmentDecision`:

```python
class FindingEnrichmentDecision(BaseModel):
    tool_calls: list[ToolCall]
    finding: ReportFinding | None
    finalize: bool
    reasoning: str


class ImpactAnalysisDecision(BaseModel):
    tool_calls: list[ToolCall]
    narrative: str
    use_cases_impacted: list[str]
    finalize: bool
    reasoning: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_result_models.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/models/results.py tests/unit/test_result_models.py
git commit -m "feat: extend BlastRadiusSummary with narrative fields, add ImpactAnalysisDecision"
```

---

### Task 2: `report/agents/impact_analysis_agent.py` — nested ReAct agent

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/report/agents/impact_analysis_agent.py`
- Test: `apps/backend/tests/unit/test_impact_analysis_agent.py`

**Interfaces:**
- Consumes: `ImpactAnalysisDecision`, `BlastRadiusSummary` (Task 1); `make_blast_radius_tool` (`src/main_graph/tools/blast_radius.py`, existing, unchanged); `read_file` (`src/main_graph/tools/package_files.py`, existing, unchanged); `_store_cache`/`is_indexable_source_file` (`src/main_graph/tools/search_code.py`, existing, unchanged); `PrepResult`, `FindingNote`, `ToolCall`, `ToolResult` (existing); `_format_tool_results` (`report/agents/critique.py`, existing — reused here rather than duplicated).
- Produces: `async def analyze_impact(finding: FindingNote, prep: PrepResult, container, depth: int = 3) -> BlastRadiusSummary`; `def make_impact_analysis_tool(finding: FindingNote, prep: PrepResult, container)` (returns a LangChain `@tool`). Consumed by Task 3 (`finding_enricher_agent.py`).

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/test_impact_analysis_agent.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.tools import tool

from src.models.conductor import FindingNote, ToolCall
from src.models.results import BlastRadiusSummary, ImpactAnalysisDecision, PrepResult


def _prep(**overrides) -> PrepResult:
    defaults = dict(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={},
        discovery_summary="s",
        vector_store_id="vs-1",
        codegraph_ready=True,
    )
    return PrepResult(**{**defaults, **overrides})


def _finding() -> FindingNote:
    return FindingNote(
        dep_name="left-pad", severity="high", description="GPL-incompatible", evidence=[]
    )


def _finalize(narrative: str = "", use_cases: list[str] | None = None):
    return ImpactAnalysisDecision(
        tool_calls=[],
        narrative=narrative,
        use_cases_impacted=use_cases or [],
        finalize=True,
        reasoning="done",
    )


def _blast_radius_call(package_name: str = "left-pad") -> ImpactAnalysisDecision:
    return ImpactAnalysisDecision(
        tool_calls=[
            ToolCall(
                tool="blast_radius",
                args={"package_name": package_name},
                reason="check graph",
            )
        ],
        narrative="",
        use_cases_impacted=[],
        finalize=False,
        reasoning="checking",
    )


def _find_usage_sites_call() -> ImpactAnalysisDecision:
    return ImpactAnalysisDecision(
        tool_calls=[ToolCall(tool="find_usage_sites", args={}, reason="fallback")],
        narrative="",
        use_cases_impacted=[],
        finalize=False,
        reasoning="falling back",
    )


def _fake_blast_radius_factory(available: bool, **output_overrides):
    # make_blast_radius_tool is a SYNC factory (it returns the @tool object
    # directly; only the tool's own body is async) — the fake must match
    # that signature or _build_internal_tool_map's unawaited call returns a
    # coroutine instead of a tool.
    def _make(repo_path, container, image):
        @tool
        async def blast_radius(package_name: str, depth: int = 3) -> dict:
            if not available:
                return {"package_name": package_name, "available": False}
            return {
                "package_name": package_name,
                "available": True,
                "affected_file_count": 2,
                "affected_files": ["src/checkout.ts:10"],
                "production_file_count": 2,
                "isolated_to_tests_or_scripts": False,
                "node_count": 5,
                "depth_searched": 3,
                **output_overrides,
            }

        return blast_radius

    return _make


@pytest.mark.asyncio
async def test_analyze_impact_grounds_from_blast_radius_when_available():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[_blast_radius_call(), _finalize("Used in checkout.", ["checkout"])]
    )

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(
            impact_analysis_agent,
            "make_blast_radius_tool",
            _fake_blast_radius_factory(available=True),
        ),
    ):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(), container=None
        )

    assert summary.available is True
    assert summary.source == "codegraph"
    assert summary.affected_file_count == 2
    assert summary.affected_files == ["src/checkout.ts:10"]
    assert summary.narrative == "Used in checkout."
    assert summary.use_cases_impacted == ["checkout"]


@pytest.mark.asyncio
async def test_analyze_impact_falls_back_to_find_usage_sites_when_unavailable():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[
            _blast_radius_call(),
            _find_usage_sites_call(),
            _finalize("Used in auth.", ["auth"]),
        ]
    )

    async def fake_find_usage_sites(package_name: str) -> list[dict]:
        return [{"file": "src/auth.ts", "snippet": "import left-pad"}]

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(
            impact_analysis_agent,
            "make_blast_radius_tool",
            _fake_blast_radius_factory(available=False),
        ),
        patch.object(
            impact_analysis_agent,
            "_make_find_usage_sites_tool",
            lambda vector_store_id: fake_find_usage_sites,
        ),
    ):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(), container=None
        )

    assert summary.available is True
    assert summary.source == "semantic_search"
    assert summary.affected_files == ["src/auth.ts"]
    assert summary.affected_file_count == 1
    assert summary.narrative == "Used in auth."


@pytest.mark.asyncio
async def test_analyze_impact_returns_unavailable_when_no_tool_data():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )

    with patch.object(impact_analysis_agent, "_llm", mock_llm):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(vector_store_id=""), container=None
        )

    assert summary.available is False
    assert summary.source == "unavailable"
    assert summary.affected_files == []


@pytest.mark.asyncio
async def test_analyze_impact_forces_package_name_on_internal_tool_calls():
    """The nested loop's own LLM cannot query a different package's evidence,
    exactly like the outer finding_enricher loop can't — mirrors the same
    force-injection guarantee one level down."""
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[_blast_radius_call(package_name="other-pkg"), _finalize()]
    )

    received: dict = {}

    def _make(repo_path, container, image):  # sync, matches make_blast_radius_tool
        @tool
        async def blast_radius(package_name: str, depth: int = 3) -> dict:
            received["package_name"] = package_name
            return {"package_name": package_name, "available": True}

        return blast_radius

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(impact_analysis_agent, "make_blast_radius_tool", _make),
    ):
        await impact_analysis_agent.analyze_impact(_finding(), _prep(), container=None)

    assert received["package_name"] == "left-pad"


@pytest.mark.asyncio
async def test_analyze_impact_degrades_gracefully_on_llm_outage():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM outage")
    )

    with (
        patch.object(impact_analysis_agent, "_llm", mock_llm),
        patch.object(impact_analysis_agent, "_MAX_ITERATIONS", 2),
    ):
        summary = await impact_analysis_agent.analyze_impact(
            _finding(), _prep(vector_store_id=""), container=None
        )

    assert summary.available is False
    assert summary.source == "unavailable"
    assert summary.narrative == ""


@pytest.mark.asyncio
async def test_make_impact_analysis_tool_wraps_analyze_impact_as_dict():
    from src.main_graph.subgraphs.report.agents import impact_analysis_agent

    fake_summary = BlastRadiusSummary(available=True, source="codegraph", narrative="x")
    with patch.object(
        impact_analysis_agent, "analyze_impact", AsyncMock(return_value=fake_summary)
    ):
        tool_fn = impact_analysis_agent.make_impact_analysis_tool(
            _finding(), _prep(), container=None
        )
        result = await tool_fn.ainvoke({"depth": 3})

    assert result["available"] is True
    assert result["narrative"] == "x"


@pytest.mark.asyncio
async def test_find_usage_sites_excludes_non_source_and_unmatched_files():
    from src.main_graph.subgraphs.report.agents.impact_analysis_agent import (
        _make_find_usage_sites_tool,
    )
    from src.main_graph.tools.search_code import _store_cache

    store = MagicMock()
    store.asimilarity_search = AsyncMock(
        return_value=[
            Document(page_content='import "left-pad"', metadata={"file": "src/a.ts"}),
            Document(
                page_content='{"dependencies": {"left-pad": "1.0.0"}}',
                metadata={"file": "package.json"},
            ),
            Document(page_content="unrelated code", metadata={"file": "src/b.ts"}),
        ]
    )
    _store_cache["vs-test"] = store
    try:
        tool_fn = _make_find_usage_sites_tool("vs-test")
        results = await tool_fn.ainvoke({"package_name": "left-pad"})
    finally:
        del _store_cache["vs-test"]

    assert len(results) == 1
    assert results[0]["file"] == "src/a.ts"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_impact_analysis_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.report.agents.impact_analysis_agent'`

- [ ] **Step 3: Implement**

Create `apps/backend/src/main_graph/subgraphs/report/agents/impact_analysis_agent.py`:

```python
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import textwrap
import time
import uuid
from typing import cast

from langchain_core.tools import tool

from src.main_graph.subgraphs.report.agents.critique import _format_tool_results
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.package_files import read_file as _read_file_impl
from src.main_graph.tools.search_code import _store_cache, is_indexable_source_file
from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import BlastRadiusSummary, ImpactAnalysisDecision, PrepResult
from src.utils.config import settings
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 3
_SNIPPET_RADIUS = 150
_llm = get_llm(Model.GPT_5_4_MINI)
_BLAST_RADIUS_FIELDS = {
    "affected_file_count",
    "affected_files",
    "production_file_count",
    "isolated_to_tests_or_scripts",
    "node_count",
    "depth_searched",
}

_TOOL_DESCRIPTIONS = {
    "blast_radius": "blast_radius(depth: int = 3): real import/usage graph blast "
    "radius for this package via codegraph — affected file count/paths, whether "
    "usage is isolated to tests/scripts. Try this first.",
    "find_usage_sites": "find_usage_sites(): fuzzy semantic-search fallback; source "
    "files that actually import/use this package, with a code snippet around the "
    "match. Use only if blast_radius returned available=false.",
    "read_file": "read_file(relative_path: str): read a specific affected file's "
    "content to judge what business capability it implements. Use this on at "
    "least one affected production file before finalizing.",
}

_SYSTEM = textwrap.dedent("""\
    You are investigating the real usage impact of ONE dependency risk
    finding's package.

    Package: {dep_name}

    Available tools:
    {tool_descriptions}

    Process: call blast_radius first. If it returns available=false, call
    find_usage_sites instead. Then read_file at least one affected
    production file (skip only if none were found by either tool) before
    finalizing, so your narrative reflects what the code actually does, not
    just its path.

    When you have enough evidence, set finalize=true and write:
    - use_cases_impacted: short list of the business use cases/capabilities
      the affected code implements (derived only from what you actually
      read via read_file, never invented). Empty list if nothing was
      affected or nothing was readable.
    - narrative: 1-3 sentences summarizing the real-world impact. If no
      tool returned usable data, say the impact could not be determined —
      never guess.

    Do not report file counts, paths, or availability yourself — those are
    captured automatically from tool output, not from your response.

    After {max_iter} iterations, set finalize=true regardless of coverage.
    """).strip()


def _snippet_around_match(content: str, needle: str) -> str:
    idx = content.find(needle)
    if idx == -1:
        return content[:300]
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(content), idx + len(needle) + _SNIPPET_RADIUS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def _make_find_usage_sites_tool(vector_store_id: str):
    @tool
    async def find_usage_sites(package_name: str) -> list[dict]:
        """Find source files that import or use a specific npm package, with
        enough surrounding code to tell what business logic depends on it."""
        store = _store_cache.get(vector_store_id)
        if store is None:
            return [{"error": f"Vector store {vector_store_id} not loaded"}]
        query = f"import {package_name} require {package_name}"
        results = await store.asimilarity_search(query, k=20)
        return [
            {
                "file": doc.metadata.get("file", ""),
                "snippet": _snippet_around_match(doc.page_content, package_name),
            }
            for doc in results
            if package_name in doc.page_content
            and is_indexable_source_file(os.path.basename(doc.metadata.get("file", "")))
        ]

    return find_usage_sites


def _make_read_file_tool(repo_path: str):
    @tool
    async def read_file(relative_path: str) -> dict:
        """Read a specific file from the cloned repo."""
        return await _read_file_impl(repo_path, relative_path)

    return read_file


def _build_internal_tool_map(finding: FindingNote, prep: PrepResult, container) -> dict:
    tools: dict = {
        "blast_radius": make_blast_radius_tool(
            prep.repo_path, container, settings.codegraph_docker_image
        ),
        "read_file": _make_read_file_tool(prep.repo_path),
    }
    if prep.vector_store_id:
        tools["find_usage_sites"] = _make_find_usage_sites_tool(prep.vector_store_id)
    return tools


def _format_internal_tools(tool_map: dict) -> str:
    return "\n".join(f"- {_TOOL_DESCRIPTIONS[name]}" for name in tool_map)


def _tool_callable(fn):
    """LangChain's @tool decorator stores a sync function's body in .func
    and an async function's body in .coroutine (leaving .func None) — pick
    whichever is actually callable for signature introspection."""
    func = getattr(fn, "func", None)
    if func is not None:
        return func
    coroutine = getattr(fn, "coroutine", None)
    if coroutine is not None:
        return coroutine
    return fn


async def _run_internal_tool(tc: ToolCall, tool_map: dict, dep_name: str) -> ToolResult:
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output={},
            error=f"unknown tool: {tc.tool}",
            duration_ms=0,
        )
    kwargs = dict(tc.args)
    sig = inspect.signature(_tool_callable(fn))
    if "package_name" in sig.parameters:
        # Force-injected: this nested loop cannot fetch evidence for a
        # different package even if its own LLM tried, mirroring the outer
        # finding_enricher loop's identical guarantee.
        kwargs["package_name"] = dep_name
    try:
        output = (
            await fn.ainvoke(kwargs) if hasattr(fn, "ainvoke") else await fn(**kwargs)
        )
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=kwargs,
            output=output if isinstance(output, dict) else {"result": output},
            error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("impact_analysis: tool %s failed: %s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=kwargs,
            output={},
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _ground_from_tool_results(tool_results: list[ToolResult]) -> dict:
    """Counts/paths/source are taken verbatim from actual tool output, never
    from the nested LLM, so a hallucinated file count can't reach the
    report."""
    for tr in reversed(tool_results):
        if tr.tool == "blast_radius" and not tr.error and tr.output.get("available"):
            return {
                **{k: v for k, v in tr.output.items() if k in _BLAST_RADIUS_FIELDS},
                "available": True,
                "source": "codegraph",
            }
    for tr in reversed(tool_results):
        if tr.tool == "find_usage_sites" and not tr.error:
            # find_usage_sites returns a list[dict], not a dict — _run_internal_tool
            # wraps any non-dict tool return as {"result": <value>} (same convention
            # as the outer _run_tool), so the actual site list lives under "result".
            sites = tr.output.get("result", []) if isinstance(tr.output, dict) else []
            files = sorted(
                {r["file"] for r in sites if isinstance(r, dict) and r.get("file")}
            )
            if files:
                return {
                    "available": True,
                    "affected_file_count": len(files),
                    "affected_files": files,
                    "source": "semantic_search",
                }
    return {"available": False, "source": "unavailable"}


async def analyze_impact(
    finding: FindingNote,
    prep: PrepResult,
    container,
    depth: int = 3,
) -> BlastRadiusSummary:
    tool_map = _build_internal_tool_map(finding, prep, container)
    tool_results: list[ToolResult] = []
    narrative = ""
    use_cases_impacted: list[str] = []

    structured = _llm.with_structured_output(
        ImpactAnalysisDecision, method="function_calling"
    )

    for iteration in range(_MAX_ITERATIONS):
        system = _SYSTEM.format(
            dep_name=finding.dep_name,
            tool_descriptions=_format_internal_tools(tool_map),
            max_iter=_MAX_ITERATIONS,
        )
        prompt = (
            f"Tool results so far:\n{_format_tool_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        try:
            decision = cast(
                ImpactAnalysisDecision,
                await structured.ainvoke(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]
                ),
            )
        except AssertionError:
            raise
        except Exception as exc:
            logger.warning(
                "impact_analysis: structured decision failed, retrying: %s", exc
            )
            continue

        last = iteration == _MAX_ITERATIONS - 1
        if decision.finalize or last:
            narrative = decision.narrative
            use_cases_impacted = decision.use_cases_impacted
            break

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[
                    _run_internal_tool(tc, tool_map, finding.dep_name)
                    for tc in decision.tool_calls
                ]
            )
            tool_results.extend(new_results)

    grounded = _ground_from_tool_results(tool_results)
    return BlastRadiusSummary(
        **grounded,
        narrative=narrative,
        use_cases_impacted=use_cases_impacted,
    )


def make_impact_analysis_tool(finding: FindingNote, prep: PrepResult, container):
    @tool
    async def impact_analysis(depth: int = 3) -> dict:
        """Investigate the real usage of this finding's package: which files
        import it, whether that reaches production code, and which business
        use cases/capabilities are actually affected. Always available."""
        result = await analyze_impact(finding, prep, container, depth)
        return result.model_dump()

    return impact_analysis
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_impact_analysis_agent.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/agents/impact_analysis_agent.py tests/unit/test_impact_analysis_agent.py
git commit -m "feat: add impact_analysis_agent, nested ReAct loop for business-use-case impact"
```

---

### Task 3: Wire `impact_analysis` into `finding_enricher_agent.py`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/report/agents/finding_enricher_agent.py`
- Modify: `apps/backend/tests/unit/test_finding_enricher_agent.py`

**Interfaces:**
- Consumes: `make_impact_analysis_tool` (Task 2).
- Produces: `_build_tool_map(finding, prep, container)` (signature gains `finding`); `_grounded_impact_analysis(tool_results) -> BlastRadiusSummary | None` (replaces `_grounded_blast_radius`). Consumed by `enrich_finding` (same file, updated in this task) and Task 7 (subgraph test).

- [ ] **Step 1: Update the failing/changed tests**

In `apps/backend/tests/unit/test_finding_enricher_agent.py`, replace the two grounding tests at the bottom of the file:

```python
def test_grounded_blast_radius_returns_summary_when_available():
    from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
        _grounded_blast_radius,
    )

    tool_results = [
        ToolResult(
            id="id",
            tool="blast_radius",
            args={"package_name": "left-pad"},
            output={
                "package_name": "left-pad",
                "available": True,
                "affected_file_count": 1,
                "affected_files": ["scripts/build.js:1"],
                "production_file_count": 0,
                "isolated_to_tests_or_scripts": True,
                "node_count": 3,
                "depth_searched": 3,
            },
            error=None,
            duration_ms=1,
        )
    ]
    summary = _grounded_blast_radius(tool_results)
    assert summary is not None
    assert summary.affected_file_count == 1
    assert summary.isolated_to_tests_or_scripts is True
    assert not hasattr(summary, "package_name")


def test_grounded_blast_radius_returns_none_when_unavailable():
    from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
        _grounded_blast_radius,
    )

    assert _grounded_blast_radius([]) is None
```

with:

```python
def test_grounded_impact_analysis_returns_summary_when_present():
    from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
        _grounded_impact_analysis,
    )

    tool_results = [
        ToolResult(
            id="id",
            tool="impact_analysis",
            args={},
            output={
                "available": True,
                "affected_file_count": 1,
                "affected_files": ["scripts/build.js:1"],
                "production_file_count": 0,
                "isolated_to_tests_or_scripts": True,
                "node_count": 3,
                "depth_searched": 3,
                "use_cases_impacted": [],
                "narrative": "Only used in a build script.",
                "source": "codegraph",
            },
            error=None,
            duration_ms=1,
        )
    ]
    summary = _grounded_impact_analysis(tool_results)
    assert summary is not None
    assert summary.affected_file_count == 1
    assert summary.isolated_to_tests_or_scripts is True
    assert summary.narrative == "Only used in a build script."


def test_grounded_impact_analysis_returns_none_when_absent():
    from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
        _grounded_impact_analysis,
    )

    assert _grounded_impact_analysis([]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_finding_enricher_agent.py -v`
Expected: FAIL — `ImportError: cannot import name '_grounded_impact_analysis'`

- [ ] **Step 3: Implement**

In `apps/backend/src/main_graph/subgraphs/report/agents/finding_enricher_agent.py`, replace the imports:

```python
from src.main_graph.subgraphs.report.agents.critique import critique_report_finding
from src.main_graph.tools.blast_radius import make_blast_radius_tool
from src.main_graph.tools.code_impact import make_code_impact_tool
from src.main_graph.tools.external_api import web_search
from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import (
    BlastRadiusSummary,
    FindingEnrichmentDecision,
    PrepResult,
    ReportFinding,
)
from src.utils.config import settings
from src.utils.llm import Model, get_llm
```

with:

```python
from src.main_graph.subgraphs.report.agents.critique import critique_report_finding
from src.main_graph.subgraphs.report.agents.impact_analysis_agent import (
    make_impact_analysis_tool,
)
from src.main_graph.tools.external_api import web_search
from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import (
    BlastRadiusSummary,
    FindingEnrichmentDecision,
    PrepResult,
    ReportFinding,
)
from src.utils.llm import Model, get_llm
```

(`settings` is no longer used directly in this file — `impact_analysis_agent.py` owns the codegraph-image lookup now.)

Replace:

```python
_MAX_ITERATIONS = 4
_llm = get_llm(Model.GPT_5_4_MINI)
_BLAST_RADIUS_FIELDS = set(BlastRadiusSummary.model_fields)

_TOOL_DESCRIPTIONS = {
    "web_search": "web_search(query: str): search the web for advisories/issues/"
    "releases about this finding's SPECIFIC flagged reason (never a generic query)",
    "blast_radius": "blast_radius(depth: int = 3): real import/usage graph blast "
    "radius for this finding's package — affected file count/paths, whether usage "
    "is isolated to tests/scripts",
    "code_impact": "code_impact(): fuzzy semantic-search fallback; source files "
    "importing this finding's package. Use only if blast_radius is unavailable.",
}
```

with:

```python
_MAX_ITERATIONS = 4
_llm = get_llm(Model.GPT_5_4_MINI)

_TOOL_DESCRIPTIONS = {
    "web_search": "web_search(query: str): search the web for advisories/issues/"
    "releases about this finding's SPECIFIC flagged reason (never a generic query)",
    "impact_analysis": "impact_analysis(depth: int = 3): investigates this "
    "package's real usage impact — affected files, whether usage reaches "
    "production, and which business use cases are actually affected. Always "
    "available.",
}
```

In `_SYSTEM`, replace:

```python
    - business_impact: derived from blast_radius/code_impact data if present
      (affected_file_count, isolated_to_tests_or_scripts, or the business
      capability the affected code implements). If neither tool returned
      anything, say the business impact could not be determined — never
      invent file counts or guess.
    - evidence: only cite results that actually discuss this finding's own
      reason, never a generic tutorial that happens to mention the package.
    - affected_files: from blast_radius/code_impact output, if any.
```

with:

```python
    - business_impact: derived from impact_analysis's narrative/
      use_cases_impacted if present. If impact_analysis could not determine
      impact, say so — never invent file counts or guess.
    - evidence: only cite results that actually discuss this finding's own
      reason, never a generic tutorial that happens to mention the package.
    - affected_files: from impact_analysis output, if any.
```

Replace:

```python
def _build_tool_map(prep: PrepResult, container) -> dict:
    tools: dict = {"web_search": web_search}
    if prep.codegraph_ready:
        tools["blast_radius"] = make_blast_radius_tool(
            prep.repo_path, container, settings.codegraph_docker_image
        )
    if prep.vector_store_id:
        tools["code_impact"] = make_code_impact_tool(prep.vector_store_id)
    return tools
```

with:

```python
def _build_tool_map(finding: FindingNote, prep: PrepResult, container) -> dict:
    return {
        "web_search": web_search,
        "impact_analysis": make_impact_analysis_tool(finding, prep, container),
    }
```

Replace:

```python
def _grounded_blast_radius(tool_results: list[ToolResult]) -> BlastRadiusSummary | None:
    for tr in tool_results:
        if tr.tool == "blast_radius" and not tr.error and tr.output.get("available"):
            return BlastRadiusSummary(
                **{k: v for k, v in tr.output.items() if k in _BLAST_RADIUS_FIELDS}
            )
    return None
```

with:

```python
def _grounded_impact_analysis(tool_results: list[ToolResult]) -> BlastRadiusSummary | None:
    for tr in tool_results:
        if tr.tool == "impact_analysis" and not tr.error:
            return BlastRadiusSummary(**tr.output)
    return None
```

Inside `enrich_finding`, replace:

```python
    tool_map = _build_tool_map(prep, container)
```

with:

```python
    tool_map = _build_tool_map(finding, prep, container)
```

and replace:

```python
            grounded = _grounded_blast_radius(tool_results)
```

with:

```python
            grounded = _grounded_impact_analysis(tool_results)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_finding_enricher_agent.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/agents/finding_enricher_agent.py tests/unit/test_finding_enricher_agent.py
git commit -m "feat: wire impact_analysis into finding_enricher, drop codegraph_ready gate"
```

---

### Task 4: `critique.py` prompt update

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/report/agents/critique.py`

**Interfaces:** none new — text-only change, existing tests in `tests/unit/test_report_critique.py` are unaffected (they don't assert exact prompt wording) and must still pass.

- [ ] **Step 1: Implement**

In `apps/backend/src/main_graph/subgraphs/report/agents/critique.py`, in `_SYSTEM`, replace:

```python
    - business_impact must be grounded in blast_radius/code_impact output
      present in tool_results. If neither tool returned data, business_impact
      should say so rather than guess — flag it if it guesses instead.
```

with:

```python
    - business_impact must be grounded in impact_analysis output (its
      narrative/use_cases_impacted, not invented) present in tool_results.
      If impact_analysis returned nothing usable, business_impact should say
      so rather than guess — flag it if it guesses instead.
```

- [ ] **Step 2: Run tests to verify nothing broke**

Run: `cd apps/backend && uv run pytest tests/unit/test_report_critique.py -v`
Expected: PASS (unchanged — no test asserts exact prompt text)

- [ ] **Step 3: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/agents/critique.py
git commit -m "docs: update critique prompt to reference impact_analysis"
```

---

### Task 5: Startup health check (`src/main.py`)

**Files:**
- Modify: `apps/backend/src/main.py`
- Test: `apps/backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Consumes: `get_client` (`src/db/connection.py`, existing, unchanged); `DockerContainerAdapter` (`src/main_graph/adapters/docker_container_adapter.py`, existing, unchanged); `settings.codegraph_docker_image` (existing).
- Produces: `lifespan(app: FastAPI)` async context manager, importable as `src.main.lifespan` for testing; `app = FastAPI(lifespan=lifespan)`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/test_main_lifespan.py`:

```python
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")


@pytest.mark.asyncio
async def test_lifespan_succeeds_when_mongo_and_codegraph_healthy():
    from src.main import lifespan

    mock_client = MagicMock()
    mock_client.admin.command = AsyncMock(return_value={"ok": 1})

    with (
        patch("src.main.get_client", return_value=mock_client),
        patch("src.main.DockerContainerAdapter") as mock_adapter_cls,
    ):
        mock_adapter_cls.return_value.run = AsyncMock(return_value=(0, "v1.0", ""))
        async with lifespan(MagicMock()):
            pass  # no exception means startup succeeded


@pytest.mark.asyncio
async def test_lifespan_raises_when_codegraph_image_broken():
    from src.main import lifespan

    mock_client = MagicMock()
    mock_client.admin.command = AsyncMock(return_value={"ok": 1})

    with (
        patch("src.main.get_client", return_value=mock_client),
        patch("src.main.DockerContainerAdapter") as mock_adapter_cls,
    ):
        mock_adapter_cls.return_value.run = AsyncMock(
            return_value=(1, "", "no such image")
        )
        with pytest.raises(RuntimeError, match="codegraph"):
            async with lifespan(MagicMock()):
                pass


@pytest.mark.asyncio
async def test_lifespan_raises_when_mongo_unreachable():
    from src.main import lifespan

    mock_client = MagicMock()
    mock_client.admin.command = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("src.main.get_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="connection refused"):
            async with lifespan(MagicMock()):
                pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_main_lifespan.py -v`
Expected: FAIL — `ImportError: cannot import name 'lifespan' from 'src.main'`

- [ ] **Step 3: Implement**

Replace the full contents of `apps/backend/src/main.py`:

```python
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.db.connection import get_client
from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
from src.utils.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if settings.langsmith_api_key:
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_TRACING"] = "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_client().admin.command("ping")
    logger.info("startup check: MongoDB reachable")

    rc, _, stderr = await DockerContainerAdapter().run(
        image=settings.codegraph_docker_image, command="codegraph --version"
    )
    if rc != 0:
        raise RuntimeError(
            f"codegraph image '{settings.codegraph_docker_image}' is not "
            f"runnable (exit {rc}): {stderr}"
        )
    logger.info("startup check: codegraph image runnable")

    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_main_lifespan.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Verify the existing integration test still passes unaffected**

Run: `cd apps/backend && uv run pytest tests/integration/test_analyze.py -v`
Expected: PASS — `httpx.AsyncClient(transport=ASGITransport(app=app))` does not trigger ASGI lifespan events by default, so this test's `client` fixture never invokes the new `lifespan` hook (confirmed by reading the test: it never enters an `async with` lifespan-managed context). If this assumption is wrong and the test now hangs or fails on Mongo/docker connectivity, add mocks for `get_client`/`DockerContainerAdapter` to that file's fixtures rather than removing the startup check.

- [ ] **Step 6: Commit**

```bash
cd apps/backend && git add src/main.py tests/unit/test_main_lifespan.py
git commit -m "feat: add startup health check for MongoDB and codegraph image"
```

---

### Task 6: Delete `code_impact.py`

**Files:**
- Delete: `apps/backend/src/main_graph/tools/code_impact.py`
- Modify: `apps/backend/tests/unit/tools/test_search_code.py` (remove the one test that covers the deleted module)

**Interfaces:** none — `code_impact.py`'s only functional caller was `finding_enricher_agent.py`, already updated in Task 3; its logic was relocated into `impact_analysis_agent.py`'s `_make_find_usage_sites_tool` in Task 2.

- [ ] **Step 1: Verify no remaining references**

Run: `cd apps/backend && grep -rn "code_impact" --include="*.py" src/ tests/`
Expected: only `tests/unit/tools/test_search_code.py`'s `test_make_code_impact_tool_returns_tool` (to be removed in this task) and the comment in `search_code.py` mentioning `code_impact` by name in a docstring about lockfile exclusion (leave the comment — `search_code.py`'s exclusion logic is still shared with the new `find_usage_sites`, the comment's point still holds even though the caller moved).

- [ ] **Step 2: Remove the module and its test**

```bash
cd apps/backend && git rm src/main_graph/tools/code_impact.py
```

In `apps/backend/tests/unit/tools/test_search_code.py`, remove:

```python
def test_make_code_impact_tool_returns_tool():
    from src.main_graph.tools.code_impact import make_code_impact_tool

    tool = make_code_impact_tool("vs-test")
    assert tool.name == "code_impact"
```

- [ ] **Step 3: Run tests to verify nothing broke**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_search_code.py -v`
Expected: PASS (remaining tests in the file)

- [ ] **Step 4: Commit**

```bash
cd apps/backend && git add -A
git commit -m "chore: delete code_impact.py, superseded by impact_analysis_agent.find_usage_sites"
```

---

### Task 7: Update `tests/subgraphs/test_report_subgraph.py`'s codegraph grounding test

**Files:**
- Modify: `apps/backend/tests/subgraphs/test_report_subgraph.py`

**Interfaces:**
- Consumes: `impact_analysis_agent` module (Task 2, patched at `_llm`), `finding_enricher_agent` module (Task 3, patched at `_llm`).

- [ ] **Step 1: Update imports**

At the top of `apps/backend/tests/subgraphs/test_report_subgraph.py`, add the new import alongside the existing `finding_enricher_agent` import:

```python
from src.main_graph.subgraphs.report.agents import finding_enricher_agent
```

becomes:

```python
from src.main_graph.subgraphs.report.agents import (
    finding_enricher_agent,
    impact_analysis_agent,
)
```

- [ ] **Step 2: Rewrite `test_report_grounds_blast_radius_via_codegraph`**

Replace the full test (it currently mocks a `blast_radius` tool call directly on `finding_enricher_agent._llm`; it now needs the outer LLM to request `impact_analysis`, and the nested `impact_analysis_agent._llm` to drive its own `blast_radius` call against the same mocked container):

```python
@pytest.mark.asyncio
async def test_report_grounds_blast_radius_via_codegraph(subgraph_config, result_dao):
    """finding_enricher's impact_analysis tool call -> nested agent's own
    blast_radius call -> container port -> the resulting draft's
    blast_radius/affected_files come from the real tool output, not either
    LLM's placeholder text."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"
    findings = [
        FindingNote(
            dep_name="left-pad",
            severity="high",
            description="GPL-incompatible copyleft dependency",
            evidence=[],
        )
    ]
    analysis = _seed_analysis(job_id, findings=findings)
    await result_dao.save_analysis(analysis)

    prep = PrepResult(
        job_id=job_id,
        repo_path="/tmp/fake-repo",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={},
        discovery_summary="",
        vector_store_id="",
        codegraph_ready=True,
    )
    prep_result_id = await result_dao.save_prep(prep)

    codegraph_output = {
        "symbol": "left-pad",
        "depth": 3,
        "nodeCount": 1,
        "edgeCount": 0,
        "affected": [
            {
                "name": "left-pad",
                "kind": "import",
                "filePath": "scripts/build.js",
                "startLine": 1,
            }
        ],
    }
    subgraph_config["configurable"]["container"].run = AsyncMock(
        return_value=(0, json.dumps(codegraph_output), "")
    )

    from src.models.conductor import ToolCall
    from src.models.results import ImpactAnalysisDecision

    outer_tool_call_decision = FindingEnrichmentDecision(
        tool_calls=[
            ToolCall(tool="impact_analysis", args={}, reason="check real usage")
        ],
        finding=None,
        finalize=False,
        reasoning="enrich with impact analysis",
    )
    outer_final_decision = _finalize(
        ReportFinding(
            dep_name="left-pad",
            severity="high",
            description="GPL-incompatible copyleft dependency",
            recommendation="Remove or replace left-pad",
            affected_files=["should-be-overwritten.js:1"],
            business_impact="Only used in a build script, never shipped.",
        )
    )

    mock_outer_llm = MagicMock()
    mock_outer_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[outer_tool_call_decision, outer_final_decision]
    )

    inner_tool_call_decision = ImpactAnalysisDecision(
        tool_calls=[
            ToolCall(tool="blast_radius", args={}, reason="check graph depth")
        ],
        narrative="",
        use_cases_impacted=[],
        finalize=False,
        reasoning="checking",
    )
    inner_final_decision = ImpactAnalysisDecision(
        tool_calls=[],
        narrative="Only used in a build script, never shipped.",
        use_cases_impacted=[],
        finalize=True,
        reasoning="done",
    )
    mock_inner_llm = MagicMock()
    mock_inner_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[inner_tool_call_decision, inner_final_decision]
    )

    synth_payload = {
        "executive_summary": "left-pad is GPL-incompatible but low exposure.",
        "recommendations": ["Replace left-pad with String.prototype.padStart"],
    }

    with (
        patch.object(finding_enricher_agent, "_llm", mock_outer_llm),
        patch.object(impact_analysis_agent, "_llm", mock_inner_llm),
        patch.object(
            finding_enricher_agent,
            "critique_report_finding",
            AsyncMock(return_value=_ok_verdict()),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
            _make_synthesizer_llm(synth_payload),
        ),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "license compliance",
                "prep_result_id": prep_result_id,
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    finding = report.findings[0]
    assert finding.blast_radius is not None
    assert finding.blast_radius.available is True
    assert finding.blast_radius.source == "codegraph"
    assert finding.blast_radius.isolated_to_tests_or_scripts is True
    assert finding.blast_radius.narrative == "Only used in a build script, never shipped."
    # grounded from the real tool output, not either LLM's placeholder text
    assert finding.affected_files == ["scripts/build.js:1"]
```

- [ ] **Step 3: Run the subgraph tests**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_report_subgraph.py -v`
Expected: PASS (all 5 tests). Requires the MongoDB testcontainer used by `result_dao`/`subgraph_config` fixtures (unchanged from before this plan).

- [ ] **Step 4: Commit**

```bash
cd apps/backend && git add tests/subgraphs/test_report_subgraph.py
git commit -m "test: update codegraph grounding test for nested impact_analysis"
```

---

### Task 8: Update `docs/backend/report.md`

**Files:**
- Modify: `docs/backend/report.md` — this path is relative to the **worktree/repo root**, NOT `apps/backend/` like every other task in this plan. Run this task's commands from the worktree root; do not `cd apps/backend` first.

**Interfaces:** none — documentation only.

- [ ] **Step 1: Add the `BlastRadiusSummary` shape**

In `docs/backend/report.md`, after the `## ReportFinding` section (after its closing fence and before the `## RiskFinding` section), insert this content verbatim:

---START INSERT---
---

## BlastRadiusSummary

Populated on `ReportFinding.blast_radius` when the per-finding enrichment
agent's `impact_analysis` tool ran. `null` only if enrichment itself never
completed (e.g. total LLM outage for that finding).

```typescript
interface BlastRadiusSummary {
  available: boolean;                 // false only if neither codegraph nor semantic search found anything
  affected_file_count: number;
  affected_files: string[];           // "path:line" entries
  production_file_count: number;
  isolated_to_tests_or_scripts: boolean;
  node_count: number;                 // 0 when source is not "codegraph"
  depth_searched: number;             // 0 when source is not "codegraph"
  use_cases_impacted: string[];       // business capabilities the affected code implements
  narrative: string;                  // 1-3 sentence real-world impact summary
  source: "codegraph" | "semantic_search" | "unavailable";
}
```

Note: `ReportFinding` above (`risk_score`, `confidence`, `summary`,
`supporting_evidence_count`, `contradictions_count`, `missing_evidence`)
predates the per-finding-agent refactor and no longer matches
`src/models/results.py`'s current `ReportFinding` shape (which also has
`business_impact`, `evidence`, `trust`, `observation`, `blast_radius`).
Full resync of that section is out of scope for this change — flagged here
for a follow-up doc pass.
---END INSERT---

- [ ] **Step 2: Commit**

Run from the worktree root (the directory containing both `apps/` and `docs/`):

```bash
git add docs/backend/report.md
git commit -m "docs: document BlastRadiusSummary shape"
```

---

### Task 9: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: PASS, zero failures.

- [ ] **Step 2: Run lint and typecheck**

Run: `cd apps/backend && uv run ruff check .`
Expected: no errors — in particular no unused imports left in `finding_enricher_agent.py` (`settings`, `make_blast_radius_tool`, `make_code_impact_tool` all removed in Task 3) or `results.py`.

Run: `cd apps/backend && uv run mypy .`
Expected: no new type errors introduced by this plan.

- [ ] **Step 3: Grep for stale references**

Run: `cd apps/backend && grep -rn "code_impact\|_grounded_blast_radius\b" --include="*.py" .`
Expected: no output.

Run: `cd apps/backend && grep -rn "prep\.codegraph_ready" --include="*.py" src/main_graph/subgraphs/report/`
Expected: no output (the report subgraph no longer reads this field anywhere; `discovery/` subgraph's own use of `codegraph_ready` is untouched and out of scope).

- [ ] **Step 4: Manually smoke-test via `run_subgraph.py`**

If a MongoDB instance, Docker, and a prior `AnalysisResult`/`PrepResult` are available locally (see `apps/backend/docs/development-setup.md`):

Run: `cd apps/backend && uv run python scripts/run_subgraph.py report --job-id smoke-test --prep-result-id <existing-prep-id> --analysis-result-id <existing-analysis-id> --concern "security review"`
Expected: prints a `ReportResult` whose findings' `blast_radius.narrative`/`use_cases_impacted` are populated (not just counts), confirming the nested agent runs end-to-end against a real LLM, real codegraph container, and real MongoDB.

- [ ] **Step 5: Final commit (if any cleanup was needed from Steps 2–3)**

```bash
cd apps/backend && git add -A
git commit -m "chore: fix lint/typecheck findings from impact-analysis-agent work"
```

(Skip this step if Steps 2–3 found nothing to fix.)
