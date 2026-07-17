# Report Subgraph Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `src/main_graph/subgraphs/report/` up to the architectural standard already established in `src/main_graph/subgraphs/analysis/`, fixing one correctness bug and two maintainability gaps identified by code review.

**Architecture:** No new subgraph nodes or fan-out — the report subgraph's single-loop shape (`report_conductor` ⇄ `report_tool_runner` → `save_report_result`) is correct and stays as-is (a report is one cohesive document; the analysis subgraph's `Send`-based parallel fan-out is not applicable here). Three independent fixes:
1. `save_report_result` switches from raw `.ainvoke()` + manual `parse_llm_json()` to `with_structured_output()`, matching every other LLM call in both subgraphs.
2. `report_tool_runner`'s if/elif tool dispatch becomes a `register()`-style dict, mirroring `src/main_graph/tools/registry.py`'s existing pattern, and `report_conductor`'s system prompt roster is generated from that same registry so the two can't drift apart (mirrors `analysis_conductor._build_system` + `get_agent_descriptions()`).
3. `save_report_result` persists `tool_results` to the job artifact via `job_repo.update_artifact_data`, matching `save_analysis_result`'s `agent_calls` persistence, for observability parity.

**Tech Stack:** Python 3.12, LangGraph 1.x, LangChain (`with_structured_output(..., method="function_calling")`), Pydantic v2, pytest + pytest-asyncio, MongoDB via testcontainers for blackbox subgraph tests, `uv` package manager, `ruff` for lint.

## Global Constraints

- Package manager: `uv` — every command is `uv run <cmd>`, never bare `python`/`pytest`.
- No new dependencies. Everything needed (`langchain_core`, `pydantic`) is already in use in these files.
- Preserve existing tested behavior: `overall_risk_level` on `ReportResult` MUST continue to be derived deterministically from `analysis.findings` severities (via `_SEVERITY_ORDER`/`max()`), never from the LLM's own output — this is asserted by `tests/subgraphs/test_report_subgraph.py::test_report_overall_risk_derived_from_findings_on_llm_failure` and is intentional, not a bug.
- Preserve the existing fallback behavior on LLM failure: `save_report_result` must still degrade to `ReportFinding(..., recommendation="Review manually")` built directly from `analysis.findings` when the LLM call fails — just triggered by an `Exception` from `with_structured_output().ainvoke()` instead of a JSON-parse `Exception`.
- Blackbox tests under `tests/subgraphs/` require Docker (testcontainers MongoDB) — start it first: `colima start` (or ensure Docker Desktop is running). They auto-skip if Docker is unavailable; don't rely on that skip to call a task "done" — confirm Docker is up before running these steps.
- Delete code once it becomes dead in the same task that orphans it — do not leave `parse_llm_json` unused in `src/utils/llm.py`.

---

### Task 1: Structured output for `save_report_result`

Fixes the most severe issue: on any LLM JSON hiccup, the current code silently discards `executive_summary`, `recommendations`, `alternatives`, `affected_files`, `evidence` for every finding. `with_structured_output()` is what every other conductor in this codebase already uses for exactly this reason.

**Files:**
- Modify: `src/models/results.py` (append after line 103)
- Modify: `src/main_graph/subgraphs/report/nodes/save_report_result.py` (full rewrite)
- Modify: `src/utils/llm.py:13,15,43-51` (remove now-dead `parse_llm_json` and its now-unused imports)
- Modify: `tests/subgraphs/test_report_subgraph.py` (update `_make_save_llm` helper + the LLM-failure test to mock the new call shape)
- Test: `tests/subgraphs/test_report_subgraph.py`

**Interfaces:**
- Produces: `ReportDraft` (new Pydantic model in `src/models/results.py`) — fields `executive_summary: str`, `overall_risk_level: str`, `findings: list[ReportFinding] = []`, `recommendations: list[str] = []`. Consumed by `save_report_result` only.
- `save_report_result(state, config) -> dict` keeps its existing signature and return shape (`{"report_result_id": str}`) — no callers outside this file need to change.

- [ ] **Step 1: Add the `ReportDraft` model**

Append to `src/models/results.py` (after the `ReportResult` class, end of file):

```python

class ReportDraft(BaseModel):
    executive_summary: str
    overall_risk_level: str
    findings: list[ReportFinding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Write the failing tests — update the blackbox report tests to the new mock shape**

Edit `tests/subgraphs/test_report_subgraph.py`. First, drop the now-unused `import json` (line 16) and update the import block:

```python
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.conductor import FindingNote
from src.models.results import AnalysisResult, ReportConductorDecision, ReportDraft
from src.main_graph.subgraphs.report.graph import build_report_subgraph
```

Replace the `_make_save_llm` helper (was lines 61–66):

```python
def _make_save_llm(report_json: dict):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=ReportDraft(**report_json))
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm
```

Replace the `broken_llm` construction inside `test_report_overall_risk_derived_from_findings_on_llm_failure` (was lines 148–149):

```python
    broken_chain = MagicMock()
    broken_chain.ainvoke = AsyncMock(side_effect=ValueError("invalid structured output"))
    broken_llm = MagicMock()
    broken_llm.with_structured_output = MagicMock(return_value=broken_chain)
```

The rest of that test file (the three `async def test_...` bodies' assertions, the `patch(...)` blocks, `_seed_analysis`) is unchanged.

- [ ] **Step 2b: Run tests to verify they fail against the current implementation**

Run: `uv run pytest tests/subgraphs/test_report_subgraph.py -v`
Expected: FAIL — `save_report_result` still calls `_llm.ainvoke(...)` directly, so `MagicMock` (which has no real `.content` attribute matching expectations) or the mocked `with_structured_output` path being unused will cause `test_report_produces_report_result` and the failure test to fail (e.g. `AttributeError` or an assertion mismatch on `executive_summary`/`overall_risk_level`). If Docker isn't running, these tests will report SKIPPED instead — start Docker (`colima start`) and rerun before treating this as a pass.

- [ ] **Step 3: Rewrite `save_report_result.py` to use structured output**

Replace the full contents of `src/main_graph/subgraphs/report/nodes/save_report_result.py`:

```python
from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.models.conductor import ToolResult
from src.models.results import AnalysisResult, ReportDraft, ReportFinding, ReportResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_SYSTEM = """\
You are a technical report writer. Given dependency risk findings and enrichment data
(web search results + code impact), produce a report.

For each finding, provide a concise description, an actionable recommendation,
safer alternatives if any, affected files if known, and supporting evidence
(tool name, url if any, and a short log excerpt).
"""


def _format_enrichment(tool_results: list[ToolResult]) -> str:
    return "\n\n".join(
        f"[{tr.tool}({json.dumps(tr.args)})] -> {json.dumps(tr.output, indent=2)[:1500]}"
        for tr in tool_results if not tr.error
    )


async def save_report_result(state, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])
    tool_results: list[ToolResult] = state.get("tool_results") or []

    findings_json = json.dumps([f.model_dump() for f in analysis.findings], indent=2)
    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings:\n{findings_json}\n\n"
        f"Enrichment data:\n{_format_enrichment(tool_results) or 'None'}"
    )

    structured = _llm.with_structured_output(ReportDraft, method="function_calling")
    try:
        draft: ReportDraft = await structured.ainvoke([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_prompt},
        ])
        findings = draft.findings
        executive_summary = draft.executive_summary
        recommendations = draft.recommendations
    except Exception as exc:
        logger.warning("save_report_result: structured output failed: %s", exc)
        findings = [
            ReportFinding(
                dep_name=f.dep_name,
                severity=f.severity,
                description=f.description,
                recommendation="Review manually",
            )
            for f in analysis.findings
        ]
        executive_summary = ""
        recommendations = []

    overall = max(
        (f.severity for f in analysis.findings),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
        default="none",
    )

    result = ReportResult(
        job_id=state["job_id"],
        concern=state["concern"],
        executive_summary=executive_summary,
        overall_risk_level=overall,
        findings=findings,
        recommendations=recommendations,
    )
    report_result_id = await dao.save_report(result)
    logger.info("save_report_result: saved report_result_id=%s findings=%d",
                report_result_id, len(findings))
    return {"report_result_id": report_result_id}
```

- [ ] **Step 4: Remove the now-dead `parse_llm_json` from `src/utils/llm.py`**

Delete lines 43–51 (the whole `parse_llm_json` function). Update the imports at the top (lines 13, 15) — remove `import json` and `from typing import Any` (both become unused once `parse_llm_json` is gone):

```python
"""LLM factory and response utilities.

Consumers pick a model from the `Model` enum and call `get_llm(model)`.
Adding a new provider means adding enum members and a branch in `get_llm` —
nothing else changes.

Install notes per provider:
    OpenAI:    langchain-openai (already in deps)
    Anthropic: uv add langchain-anthropic
    Google:    uv add langchain-google-genai
"""

from enum import StrEnum

from langchain_core.language_models import BaseChatModel

from src.utils.config import settings


class Model(StrEnum):
    # OpenAI
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4O = "gpt-4o"
    GPT_5_4_NANO = "gpt-5.4-nano-2026-03-17"
    GPT_5_4_MINI = "gpt-5.4-mini-2026-03-17"
    GPT_5_4 = "gpt-5.5-2026-04-23"


def get_llm(model: Model = Model.GPT_4O_MINI) -> BaseChatModel:
    """Return a configured chat model for the given ``model`` enum value.

    Providers other than OpenAI require their package to be installed first.
    Importing is deferred so missing packages only raise at call time, not at
    import time for callers that never use those models.
    """
    from langchain_openai import ChatOpenAI  # noqa: PLC0415

    return ChatOpenAI(model=model.value, api_key=settings.openai_api_key, temperature=0)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/subgraphs/test_report_subgraph.py -v`
Expected: PASS — all 3 tests (`test_report_produces_report_result`, `test_report_overall_risk_derived_from_findings_on_llm_failure`, `test_report_with_empty_findings`) green.

- [ ] **Step 6: Lint check**

Run: `uv run ruff check src/models/results.py src/main_graph/subgraphs/report/nodes/save_report_result.py src/utils/llm.py tests/subgraphs/test_report_subgraph.py`
Expected: no errors (confirms the removed imports in `llm.py` are actually gone and nothing else broke).

- [ ] **Step 7: Commit**

```bash
git add src/models/results.py src/main_graph/subgraphs/report/nodes/save_report_result.py src/utils/llm.py tests/subgraphs/test_report_subgraph.py
git commit -m "fix: use structured output in save_report_result instead of manual JSON parsing"
```

---

### Task 2: Registry-based tool dispatch for `report_tool_runner`

Replaces the if/elif chain in `_run_one` with a `register()`-style dict, mirroring the pattern already established in `src/main_graph/tools/registry.py` (used for `web_search`, `github_advisory`, etc.) rather than inventing a new style.

**Files:**
- Create: `src/main_graph/subgraphs/report/utils/__init__.py`
- Create: `src/main_graph/subgraphs/report/utils/registry.py`
- Modify: `src/main_graph/subgraphs/report/nodes/report_tool_runner.py` (full rewrite)
- Test: `tests/unit/test_report_tool_runner.py` (new)

**Interfaces:**
- Produces: `REPORT_TOOL_HANDLERS: dict[str, Callable[[dict, PrepResult, AnalysisResult], Awaitable[dict]]]` and `REPORT_TOOL_DESCRIPTIONS: dict[str, str]` in `src/main_graph/subgraphs/report/utils/registry.py`. Task 3 consumes `REPORT_TOOL_DESCRIPTIONS`.
- `report_tool_runner(state, config) -> dict` keeps its existing signature and return shape (`{"tool_results": list[ToolResult]}`).

- [ ] **Step 1: Write the failing test for the new registry module**

Create `tests/unit/test_report_tool_runner.py`:

```python
from __future__ import annotations

import pytest

from src.models.conductor import FindingNote
from src.models.results import AnalysisResult, PrepResult


def _prep() -> PrepResult:
    return PrepResult(
        job_id="j1", repo_path="/tmp/repo", project_metadata={},
        manifest_files=[], detected_package_manager="npm",
        dependency_graph={}, discovery_summary="", vector_store_id="",
    )


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        job_id="j1", concern="c",
        findings=[
            FindingNote(dep_name="lodash", severity="high", description="d", evidence=[]),
            FindingNote(dep_name="axios", severity="low", description="d2", evidence=[]),
        ],
        evidence_bundle_ids=[], iteration_count=1,
    )


def test_registry_has_expected_tools():
    from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_HANDLERS, REPORT_TOOL_DESCRIPTIONS
    assert set(REPORT_TOOL_HANDLERS) == {"web_search", "code_impact", "get_findings"}
    assert set(REPORT_TOOL_DESCRIPTIONS) == {"web_search", "code_impact", "get_findings"}


@pytest.mark.asyncio
async def test_get_findings_handler_filters_by_severity():
    from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_HANDLERS
    handler = REPORT_TOOL_HANDLERS["get_findings"]
    result = await handler({"severity": "high"}, _prep(), _analysis())
    assert len(result["findings"]) == 1
    assert result["findings"][0]["dep_name"] == "lodash"


@pytest.mark.asyncio
async def test_get_findings_handler_all_returns_everything():
    from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_HANDLERS
    handler = REPORT_TOOL_HANDLERS["get_findings"]
    result = await handler({"severity": "all"}, _prep(), _analysis())
    assert len(result["findings"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_report_tool_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.report.utils'`

- [ ] **Step 3: Create the registry module**

Create `src/main_graph/subgraphs/report/utils/__init__.py` (empty file).

Create `src/main_graph/subgraphs/report/utils/registry.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.main_graph.tools.code_impact import make_code_impact_tool
from src.main_graph.tools.external_api import web_search
from src.models.results import AnalysisResult, PrepResult

REPORT_TOOL_HANDLERS: dict[str, Callable[..., Awaitable[dict]]] = {}
REPORT_TOOL_DESCRIPTIONS: dict[str, str] = {}


def _register(name: str, description: str):
    def decorator(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        REPORT_TOOL_HANDLERS[name] = fn
        REPORT_TOOL_DESCRIPTIONS[name] = description
        return fn
    return decorator


@_register("web_search", "search for alternatives, CVE details, migration guides")
async def _web_search_handler(args: dict, prep: PrepResult, analysis: AnalysisResult) -> dict:
    return await web_search(**args)


@_register("code_impact", "find source files that import or use a specific npm package")
async def _code_impact_handler(args: dict, prep: PrepResult, analysis: AnalysisResult) -> dict:
    impact_tool = make_code_impact_tool(prep.vector_store_id)
    output = await impact_tool.ainvoke(args)
    return output if isinstance(output, dict) else {"results": output}


@_register("get_findings", "retrieve findings filtered by severity (critical|high|medium|low|all)")
async def _get_findings_handler(args: dict, prep: PrepResult, analysis: AnalysisResult) -> dict:
    severity = args.get("severity", "all")
    findings = analysis.findings
    if severity != "all":
        findings = [f for f in findings if f.severity == severity]
    return {"findings": [f.model_dump() for f in findings]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_report_tool_runner.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Rewrite `report_tool_runner.py` to dispatch through the registry**

Replace the full contents of `src/main_graph/subgraphs/report/nodes/report_tool_runner.py`:

```python
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_HANDLERS
from src.models.conductor import ToolCall, ToolResult
from src.models.results import AnalysisResult, PrepResult

logger = logging.getLogger(__name__)


async def _run_one(tc: ToolCall, prep: PrepResult, analysis: AnalysisResult) -> ToolResult:
    start = time.monotonic()
    handler = REPORT_TOOL_HANDLERS.get(tc.tool)
    try:
        if handler is None:
            output = {"error": f"unknown tool: {tc.tool}"}
        else:
            output = await handler(tc.args, prep, analysis)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output=output, error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("report_tool_runner: tool=%s error=%s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


async def report_tool_runner(state, config: RunnableConfig) -> dict:
    decision = state.get("conductor_decision")
    if not decision or not decision.tool_calls:
        return {"tool_results": []}

    dao = get_services(config)["result_dao"]
    prep: PrepResult = await dao.get_prep(state["prep_result_id"])
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    results = await asyncio.gather(
        *[_run_one(tc, prep, analysis) for tc in decision.tool_calls]
    )
    return {"tool_results": list(results)}
```

Note: this is a mechanical dispatch-table swap — `_run_one`'s signature, `report_tool_runner`'s signature, and the `unknown tool: ...` error shape are all unchanged, so no existing caller or test needs to change.

- [ ] **Step 6: Run the full report test suite to confirm no regression**

Run: `uv run pytest tests/unit/test_report_tool_runner.py tests/unit/test_report_routing.py tests/subgraphs/test_report_subgraph.py -v`
Expected: PASS — all green (the blackbox tests never exercise tool dispatch directly since their conductor mocks always return `finalize=True, tool_calls=[]`, so this step is a safety net, not new coverage).

- [ ] **Step 7: Lint check**

Run: `uv run ruff check src/main_graph/subgraphs/report/utils/ src/main_graph/subgraphs/report/nodes/report_tool_runner.py tests/unit/test_report_tool_runner.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/main_graph/subgraphs/report/utils/ src/main_graph/subgraphs/report/nodes/report_tool_runner.py tests/unit/test_report_tool_runner.py
git commit -m "refactor: dispatch report_tool_runner tools through a registry instead of if/elif"
```

---

### Task 3: Sync `report_conductor`'s prompt roster to the registry

Right now the conductor's system prompt hardcodes the same 3 tool names+descriptions that Task 2's registry also defines, independently. This closes the drift: add a new tool to the registry and the conductor's roster picks it up automatically, exactly like `analysis_conductor._build_system` + `get_agent_descriptions()`.

**Files:**
- Modify: `src/main_graph/subgraphs/report/nodes/report_conductor.py` (full rewrite)
- Test: `tests/unit/test_report_conductor.py` (new)

**Interfaces:**
- Consumes: `REPORT_TOOL_DESCRIPTIONS` from `src/main_graph/subgraphs/report/utils/registry.py` (Task 2).
- Produces: `_build_system(max_iter: int) -> str` in `report_conductor.py`, mirroring `analysis_conductor._build_system`. `report_conductor(state, config) -> dict` keeps its existing signature and return shape.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_report_conductor.py`:

```python
from __future__ import annotations


def test_build_system_lists_all_registered_tools():
    from src.main_graph.subgraphs.report.nodes.report_conductor import _build_system
    from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_DESCRIPTIONS

    system = _build_system(6)

    for name in REPORT_TOOL_DESCRIPTIONS:
        assert name in system


def test_build_system_includes_max_iter():
    from src.main_graph.subgraphs.report.nodes.report_conductor import _build_system
    system = _build_system(6)
    assert "6" in system
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_report_conductor.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_system' from 'src.main_graph.subgraphs.report.nodes.report_conductor'`

- [ ] **Step 3: Rewrite `report_conductor.py` to build its roster from the registry**

Replace the full contents of `src/main_graph/subgraphs/report/nodes/report_conductor.py`:

```python
from __future__ import annotations

import json
import logging
import textwrap

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_DESCRIPTIONS
from src.models.conductor import FindingNote, ToolResult
from src.models.results import AnalysisResult, ReportConductorDecision
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 6
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_TEMPLATE = textwrap.dedent("""\
    You are a technical report writer. You enrich dependency risk findings using
    the tools below.

    For each high/critical finding, call both web_search and code_impact
    before finalizing.
    Output a ReportConductorDecision:
    - tool_calls: tools to run in parallel
    - finalize: true when all high/critical findings are enriched
    - reasoning: what you are doing

    After {max_iter} iterations, set finalize=true.

    Available tools:
    {roster}
    """).strip()


def _build_system(max_iter: int) -> str:
    roster = "\n".join(f"- {name}: {desc}" for name, desc in REPORT_TOOL_DESCRIPTIONS.items())
    return _SYSTEM_TEMPLATE.format(roster=roster, max_iter=max_iter)


def _format_results(results: list[ToolResult]) -> str:
    if not results:
        return "No tool results yet."
    parts = []
    for tr in results[-15:]:
        val = f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        parts.append(f"[{tr.tool}] → {val}")
    return "\n\n".join(parts)


def _format_findings(findings: list[FindingNote]) -> str:
    return "\n".join(
        f"- [{f.severity.upper()}] {f.dep_name}: {f.description}"
        for f in findings
    )


async def report_conductor(state, config: RunnableConfig) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1
    dao = get_services(config)["result_dao"]

    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings to enrich:\n{_format_findings(analysis.findings)}\n\n"
        f"Tool results so far:\n{_format_results(state.get('tool_results') or [])}\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )
    system = _build_system(_MAX_ITERATIONS)
    structured = _llm.with_structured_output(ReportConductorDecision, method="function_calling")
    decision: ReportConductorDecision = await structured.ainvoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ])

    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    logger.info("report_conductor: iteration=%d tools=%d finalize=%s",
                iteration, len(decision.tool_calls), decision.finalize)
    return {"conductor_decision": decision, "conductor_iteration": iteration}
```

Note: `_MAX_ITERATIONS`, `_format_results`, `_format_findings`, and `report_conductor`'s body/signature are unchanged from the current file — only the system-prompt construction moved into `_build_system`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_report_conductor.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Run the full report test suite to confirm no regression**

Run: `uv run pytest tests/unit/test_report_conductor.py tests/unit/test_report_routing.py tests/unit/test_report_tool_runner.py tests/subgraphs/test_report_subgraph.py -v`
Expected: PASS — all green.

- [ ] **Step 6: Lint check**

Run: `uv run ruff check src/main_graph/subgraphs/report/nodes/report_conductor.py tests/unit/test_report_conductor.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/main_graph/subgraphs/report/nodes/report_conductor.py tests/unit/test_report_conductor.py
git commit -m "refactor: build report_conductor's tool roster from the registry, not a hardcoded list"
```

---

### Task 4: Persist `tool_results` to the job artifact

Analysis persists `agent_calls` to the job artifact in `save_analysis_result` for tracing which agent did what; report currently drops `tool_results` on the floor after `save_report_result` runs, with no equivalent trace for debugging/observability.

**Files:**
- Modify: `src/main_graph/subgraphs/report/nodes/save_report_result.py:1-10,88-97` (add `job_repo` call before return)
- Test: `tests/subgraphs/test_report_subgraph.py` (extend `test_report_produces_report_result`)

**Interfaces:**
- Consumes: `job_repo: JobRepositoryPort` from `get_services(config)["job_repo"]`, method `update_artifact_data(job_id: str, node: str, data: dict) -> None` (already used by `save_analysis_result.py:34`).
- Consumes: `REPORT` constant from `src/main_graph/constants.py` (already exists, value `"report"`).
- `save_report_result`'s return shape is unchanged (`{"report_result_id": str}`) — this only adds a side effect before returning.

- [ ] **Step 1: Write the failing test**

Edit `tests/subgraphs/test_report_subgraph.py`, extend `test_report_produces_report_result` (append at the end of the function body, after the existing assertions):

```python
    job_repo = subgraph_config["configurable"]["job_repo"]
    job_repo.update_artifact_data.assert_awaited_once_with(
        job_id, "report", {"tool_results": []}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/subgraphs/test_report_subgraph.py::test_report_produces_report_result -v`
Expected: FAIL — `AssertionError: expected call not found` (`job_repo.update_artifact_data` was never called).

- [ ] **Step 3: Add the job artifact persistence call**

Replace the full contents of `src/main_graph/subgraphs/report/nodes/save_report_result.py`:

```python
from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.constants import REPORT
from src.models.conductor import ToolResult
from src.models.results import AnalysisResult, ReportDraft, ReportFinding, ReportResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_SYSTEM = """\
You are a technical report writer. Given dependency risk findings and enrichment data
(web search results + code impact), produce a report.

For each finding, provide a concise description, an actionable recommendation,
safer alternatives if any, affected files if known, and supporting evidence
(tool name, url if any, and a short log excerpt).
"""


def _format_enrichment(tool_results: list[ToolResult]) -> str:
    return "\n\n".join(
        f"[{tr.tool}({json.dumps(tr.args)})] -> {json.dumps(tr.output, indent=2)[:1500]}"
        for tr in tool_results if not tr.error
    )


async def save_report_result(state, config: RunnableConfig) -> dict:
    services = get_services(config)
    dao = services["result_dao"]
    job_repo = services["job_repo"]
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])
    tool_results: list[ToolResult] = state.get("tool_results") or []

    findings_json = json.dumps([f.model_dump() for f in analysis.findings], indent=2)
    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings:\n{findings_json}\n\n"
        f"Enrichment data:\n{_format_enrichment(tool_results) or 'None'}"
    )

    structured = _llm.with_structured_output(ReportDraft, method="function_calling")
    try:
        draft: ReportDraft = await structured.ainvoke([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_prompt},
        ])
        findings = draft.findings
        executive_summary = draft.executive_summary
        recommendations = draft.recommendations
    except Exception as exc:
        logger.warning("save_report_result: structured output failed: %s", exc)
        findings = [
            ReportFinding(
                dep_name=f.dep_name,
                severity=f.severity,
                description=f.description,
                recommendation="Review manually",
            )
            for f in analysis.findings
        ]
        executive_summary = ""
        recommendations = []

    overall = max(
        (f.severity for f in analysis.findings),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
        default="none",
    )

    result = ReportResult(
        job_id=state["job_id"],
        concern=state["concern"],
        executive_summary=executive_summary,
        overall_risk_level=overall,
        findings=findings,
        recommendations=recommendations,
    )
    report_result_id = await dao.save_report(result)

    await job_repo.update_artifact_data(
        state["job_id"], REPORT, {"tool_results": [tr.model_dump() for tr in tool_results]}
    )

    logger.info("save_report_result: saved report_result_id=%s findings=%d",
                report_result_id, len(findings))
    return {"report_result_id": report_result_id}
```

This is the complete file — `_format_enrichment`, `_SYSTEM`, `_SEVERITY_ORDER`, and the module-level `_llm` are carried over unchanged from Task 1's Step 3; the only additions are the `job_repo` lookup, the `REPORT` import, and the `update_artifact_data` call before the final `return`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/subgraphs/test_report_subgraph.py -v`
Expected: PASS — all 3 tests green, including the new assertion.

- [ ] **Step 5: Run the entire project test suite**

Run: `uv run pytest -v`
Expected: PASS — no regressions anywhere else in the project.

- [ ] **Step 6: Lint check**

Run: `uv run ruff check src/main_graph/subgraphs/report/ tests/subgraphs/test_report_subgraph.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/main_graph/subgraphs/report/nodes/save_report_result.py tests/subgraphs/test_report_subgraph.py
git commit -m "feat: persist report tool_results to the job artifact for observability parity with analysis"
```
