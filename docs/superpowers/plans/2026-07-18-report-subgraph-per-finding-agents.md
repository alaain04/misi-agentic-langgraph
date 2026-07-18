# Report Subgraph Per-Finding Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the report subgraph's single shared ReAct conductor with per-finding isolated subagents, mirroring the analysis subgraph's dispatcher/domain_agent/critique pattern, so each finding's evidence is structurally scoped to its own package and untrusted findings are flagged (not dropped) via `trust`/`observation`.

**Architecture:** `report_intake` (no LLM: fetch + severity-filter findings) fans out via `Send` to one `finding_enricher` subagent per finding, each running a bounded ReAct + self-critique loop (mirrors `base_agent._react_loop`) with tool calls force-pinned to that finding's own `dep_name`. Results fan back in through a no-op `enrichment_collector` to `report_synthesizer`, which writes only the report-level narrative (`executive_summary`/`recommendations`) over the already-vetted findings.

**Tech Stack:** Python 3.12+, LangGraph (`StateGraph`, `Send`), Pydantic v2, `uv`/`pytest`/`pytest-asyncio`, MongoDB (via `ResultDAO`, testcontainer-backed in subgraph tests), ruff, mypy.

## Global Constraints

- All backend commands run from `apps/backend/` (where `Makefile`/`pyproject.toml` live): `cd apps/backend && uv run <cmd>`.
- Mirror the analysis subgraph's proven pattern exactly — no batch conductor reflection, no adaptive multi-round dispatch at the top level (confirmed design decision).
- Untrusted findings (critique fails after retries) are **kept** in `ReportResult.findings` with `trust=False, observation=<critique feedback>`, never dropped. Trusted findings get `trust=True, observation=""`.
- `package_name` is force-injected into every tool call inside `finding_enricher_agent._run_tool`, exactly like `base_agent._run_tool` already force-injects `repo_path`/`docker_image`/`container` — this is the structural fix, not a new tool factory. (This is one deliberate simplification vs. the committed spec's `make_web_search_tool` factory idea: reusing the existing force-injection mechanism is simpler and avoids duplicating `web_search`'s Tavily-call logic, while providing the identical guarantee.)
- `risk_min_severity` filtering happens once in `report_intake`, **before** enrichment (not after, as today) — findings below threshold never trigger tool calls or LLM enrichment.
- No frontend changes required — verified that `start_artifact`/`complete_artifact` in `job_runner.py` are keyed only on the top-level `REPORT` constant, not internal subgraph node names.
- Spec reference: `docs/superpowers/specs/2026-07-18-report-subgraph-per-finding-agents-design.md`.

---

### Task 1: Schema changes — `trust`/`observation` on `ReportFinding`, `FindingEnrichmentDecision`

**Files:**
- Modify: `apps/backend/src/models/results.py`
- Test: `apps/backend/tests/unit/test_result_models.py`

**Interfaces:**
- Produces: `ReportFinding.trust: bool` (default `True`), `ReportFinding.observation: str` (default `""`); `FindingEnrichmentDecision(tool_calls: list[ToolCall], finding: ReportFinding | None, finalize: bool, reasoning: str)`. These are consumed by Task 4 (`finding_enricher_agent.py`).
- Note: `ReportConductorDecision` is intentionally **kept** in this task — `report_conductor.py`/`report_tool_runner.py` still import it and must stay importable until they're deleted in Task 8. It is removed there, not here, so every task's commit leaves the full test suite importable.

- [ ] **Step 1: Write the failing tests**

In `apps/backend/tests/unit/test_result_models.py`, update the import block at the top of the file from:

```python
from src.models.conductor import FindingNote
from src.models.results import (
    AgentCallRecord,
    AgentDispatch,
    AnalysisConductorDecision,
    DomainAgentDecision,
    EvidenceBundle,
    PrepResult,
    ReportFinding,
    ReportResult,
)
```

to:

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

Then append to the bottom of the same file:

```python
def test_report_finding_trust_defaults_true_with_empty_observation():
    f = ReportFinding(
        dep_name="lodash", severity="high", description="CVE",
        recommendation="upgrade",
    )
    assert f.trust is True
    assert f.observation == ""


def test_report_finding_accepts_untrusted_flag():
    f = ReportFinding(
        dep_name="lodash", severity="high", description="CVE",
        recommendation="upgrade", trust=False,
        observation="business_impact not grounded in tool output",
    )
    assert f.trust is False
    assert f.observation == "business_impact not grounded in tool output"


def test_finding_enrichment_decision_round_trip():
    d = FindingEnrichmentDecision(
        tool_calls=[], finding=None, finalize=False, reasoning="need more evidence"
    )
    assert d.finalize is False
    assert d.finding is None
```

Add `FindingEnrichmentDecision` to the existing import block at the top of the file (alongside `ReportFinding`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_result_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'FindingEnrichmentDecision'`

- [ ] **Step 3: Implement the schema changes**

In `apps/backend/src/models/results.py`, replace:

```python
class ReportFinding(BaseModel):
    dep_name: str
    severity: str
    description: str
    recommendation: str
    alternatives: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    evidence: list = Field(default_factory=list)
    business_impact: str = ""
    blast_radius: BlastRadiusSummary | None = None


class ReportConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    finalize: bool = False
    reasoning: str
```

with (note `ReportConductorDecision` is unchanged here, just kept below the new class):

```python
class ReportFinding(BaseModel):
    dep_name: str
    severity: str
    description: str
    recommendation: str
    alternatives: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    evidence: list = Field(default_factory=list)
    business_impact: str = ""
    blast_radius: BlastRadiusSummary | None = None
    trust: bool = True
    observation: str = ""


class FindingEnrichmentDecision(BaseModel):
    tool_calls: list[ToolCall]
    finding: ReportFinding | None
    finalize: bool
    reasoning: str


class ReportConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    finalize: bool = False
    reasoning: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_result_models.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/models/results.py tests/unit/test_result_models.py
git commit -m "feat: add trust/observation to ReportFinding, FindingEnrichmentDecision"
```

---

### Task 2: `ReportState` reshape

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/report/state.py`

**Interfaces:**
- Produces: `ReportState` keys `findings_to_enrich: NotRequired[list[dict]]`, `all_flagged_dep_names: NotRequired[list[str]]`, `current_finding: NotRequired[dict]`, `enriched_findings: Annotated[list[dict], operator.add]`. Consumed by Tasks 5–10.

No new tests here — `ReportState` is a `TypedDict` (structural, not runtime-validated); it's exercised end-to-end by Task 9's subgraph test.

- [ ] **Step 1: Rewrite the state file**

Replace the full contents of `apps/backend/src/main_graph/subgraphs/report/state.py`:

```python
from __future__ import annotations

import operator
from typing import Annotated, NotRequired

from typing_extensions import TypedDict


class ReportState(TypedDict):
    # From MainState
    job_id: str
    concern: str
    prep_result_id: str
    analysis_result_id: str

    # Internal
    findings_to_enrich: NotRequired[list[dict]]  # FindingNote.model_dump() list
    all_flagged_dep_names: NotRequired[list[str]]
    current_finding: NotRequired[dict]  # FindingNote.model_dump() for finding_enricher
    enriched_findings: Annotated[list[dict], operator.add]  # ReportFinding.model_dump()

    # Output
    report_result_id: NotRequired[str]
```

- [ ] **Step 2: Verify the module still imports**

Run: `cd apps/backend && uv run python -c "from src.main_graph.subgraphs.report.state import ReportState; print(ReportState.__annotations__.keys())"`
Expected: prints the key names above, no error (`report_conductor.py`/`report_tool_runner.py` still exist and still import `ReportConductorDecision` from `models/results.py`, which Task 1 deliberately kept — that pairing is only cleaned up in Task 8; this step only checks `state.py` in isolation)

- [ ] **Step 3: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/state.py
git commit -m "feat: reshape ReportState for per-finding fan-out"
```

---

### Task 3: `report/agents/critique.py` — `critique_report_finding`

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/report/agents/critique.py`
- Create: `apps/backend/src/main_graph/subgraphs/report/agents/__init__.py` (empty, mirrors `analysis/agents/__init__.py`)
- Test: `apps/backend/tests/unit/test_report_critique.py`

**Interfaces:**
- Consumes: `FindingNote` (`src.models.conductor`), `ReportFinding`/`ToolResult` (already available from Task 1 / existing models).
- Produces: `FindingVerdict(ok: bool, feedback: str, calibrated_confidence: float)`, `async def critique_report_finding(original: FindingNote, draft: ReportFinding, tool_results: list[ToolResult]) -> FindingVerdict`. Consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/test_report_critique.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.conductor import FindingNote, ToolResult
from src.models.results import ReportFinding


def _original() -> FindingNote:
    return FindingNote(
        dep_name="left-pad", severity="high", description="GPL-incompatible", evidence=[]
    )


def _draft() -> ReportFinding:
    return ReportFinding(
        dep_name="left-pad",
        severity="high",
        description="GPL-incompatible",
        recommendation="Replace with String.prototype.padStart",
    )


def _tool_result(tool: str, output: dict, error: str | None = None) -> ToolResult:
    return ToolResult(
        id="id", tool=tool, args={}, output=output, error=error, duration_ms=1
    )


def test_format_tool_results_renders_errors_and_output():
    from src.main_graph.subgraphs.report.agents.critique import _format_tool_results

    results = [
        _tool_result("web_search", {"results": ["x"]}),
        _tool_result("blast_radius", {}, error="timed out"),
    ]
    rendered = _format_tool_results(results)
    assert "[web_search]" in rendered
    assert "ERROR: timed out" in rendered


def test_format_tool_results_handles_empty():
    from src.main_graph.subgraphs.report.agents.critique import _format_tool_results

    assert _format_tool_results([]) == "(no tool results)"


@pytest.mark.asyncio
async def test_critique_report_finding_returns_verdict():
    from src.main_graph.subgraphs.report.agents.critique import (
        FindingVerdict,
        critique_report_finding,
    )

    verdict = FindingVerdict(
        ok=False, feedback="business_impact is not grounded", calibrated_confidence=0.2
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=verdict
    )

    with patch("src.main_graph.subgraphs.report.agents.critique._llm", mock_llm):
        result = await critique_report_finding(_original(), _draft(), [])

    assert result.ok is False
    assert result.calibrated_confidence == 0.2
    mock_llm.with_structured_output.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_report_critique.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.report.agents'`

- [ ] **Step 3: Implement**

Create `apps/backend/src/main_graph/subgraphs/report/agents/__init__.py` (empty file).

Create `apps/backend/src/main_graph/subgraphs/report/agents/critique.py`:

```python
from __future__ import annotations

import textwrap
from typing import cast

from pydantic import BaseModel

from src.models.conductor import FindingNote, ToolResult
from src.models.results import ReportFinding
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = textwrap.dedent("""\
    You are an evidence auditor for a dependency risk report. You are given
    one finding's original claim (from the analysis phase) and a draft
    ReportFinding an isolated enrichment agent produced from its own tool
    results.

    Judge whether the draft is adequately supported by ITS OWN tool_results:
    - evidence entries must reference something that actually appears in
      tool_results, not invented and not generic.
    - business_impact must be grounded in blast_radius/code_impact output
      present in tool_results. If neither tool returned data, business_impact
      should say so rather than guess — flag it if it guesses instead.
    - alternatives must be backed by a web_search result in tool_results.
    - severity and dep_name must be unchanged from the original finding.

    Output a FindingVerdict:
    - ok: true only if the draft is fully supported by tool_results.
    - feedback: concrete and actionable — name exactly what is missing or
      overstated. Empty string when ok is true.
    - calibrated_confidence: 0.0-1.0 based on evidence quality.
    """).strip()


class FindingVerdict(BaseModel):
    ok: bool
    feedback: str
    calibrated_confidence: float


def _format_tool_results(tool_results: list[ToolResult]) -> str:
    if not tool_results:
        return "(no tool results)"
    parts = []
    for tr in tool_results:
        val = f"ERROR: {tr.error}" if tr.error else str(tr.output)[:1000]
        parts.append(f"[{tr.tool}] {val}")
    return "\n\n".join(parts)


async def critique_report_finding(
    original: FindingNote,
    draft: ReportFinding,
    tool_results: list[ToolResult],
) -> FindingVerdict:
    user = (
        f"Original finding: {original.dep_name} [{original.severity}] "
        f"{original.description}\n\n"
        f"Draft report finding:\n{draft.model_dump_json(indent=2)}\n\n"
        f"Tool results this agent collected:\n{_format_tool_results(tool_results)}"
    )
    structured = _llm.with_structured_output(FindingVerdict, method="function_calling")
    return cast(
        FindingVerdict,
        await structured.ainvoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ]
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_report_critique.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/agents/ tests/unit/test_report_critique.py
git commit -m "feat: add critique_report_finding for per-finding evidence auditing"
```

---

### Task 4: `report/agents/finding_enricher_agent.py` — bounded ReAct + self-critique loop per finding

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/report/agents/finding_enricher_agent.py`
- Test: `apps/backend/tests/unit/test_finding_enricher_agent.py`

**Interfaces:**
- Consumes: `FindingEnrichmentDecision`, `ReportFinding` (Task 1); `critique_report_finding`, `FindingVerdict` (Task 3); `PrepResult` (existing); `make_blast_radius_tool`, `make_code_impact_tool`, `web_search` (existing, unmodified).
- Produces: `async def enrich_finding(finding: FindingNote, prep: PrepResult, all_flagged_dep_names: list[str], container=None) -> tuple[ReportFinding, list[str]]`. Consumed by Task 5 (`finding_enricher` node).

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/tests/unit/test_finding_enricher_agent.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import FindingEnrichmentDecision, PrepResult, ReportFinding


def _prep(**overrides) -> PrepResult:
    defaults = dict(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={},
        discovery_summary="s",
        vector_store_id="",
        codegraph_ready=False,
    )
    return PrepResult(**{**defaults, **overrides})


def _finding() -> FindingNote:
    return FindingNote(
        dep_name="left-pad", severity="high", description="GPL-incompatible", evidence=[]
    )


def _draft() -> ReportFinding:
    return ReportFinding(
        dep_name="left-pad",
        severity="high",
        description="GPL-incompatible",
        recommendation="Replace with String.prototype.padStart",
    )


def _finalize(finding: ReportFinding | None = None) -> FindingEnrichmentDecision:
    return FindingEnrichmentDecision(
        tool_calls=[], finding=finding or _draft(), finalize=True, reasoning="done"
    )


def _tool_call_decision() -> FindingEnrichmentDecision:
    return FindingEnrichmentDecision(
        tool_calls=[
            ToolCall(
                tool="web_search",
                args={"package_name": "other-pkg", "query": "q"},
                reason="r",
            )
        ],
        finding=None,
        finalize=False,
        reasoning="looking",
    )


def _ok_verdict():
    from src.main_graph.subgraphs.report.agents.critique import FindingVerdict

    return FindingVerdict(ok=True, feedback="", calibrated_confidence=0.9)


def _reject_verdict(feedback: str = "unsupported"):
    from src.main_graph.subgraphs.report.agents.critique import FindingVerdict

    return FindingVerdict(ok=False, feedback=feedback, calibrated_confidence=0.2)


@pytest.mark.asyncio
async def test_enrich_finding_happy_path_sets_trust_true():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is True
    assert draft.observation == ""
    assert tools_used == []


@pytest.mark.asyncio
async def test_enrich_finding_forces_package_name_on_tool_calls():
    """A subagent cannot fetch evidence for a different package even if the
    LLM passes one — package_name is force-injected server-side."""
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[_tool_call_decision(), _finalize()]
    )
    critic = AsyncMock(return_value=_ok_verdict())

    received: dict = {}

    async def fake_web_search(package_name: str, query: str) -> dict:
        received["package_name"] = package_name
        return {"results": []}

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
        patch.object(finding_enricher_agent, "web_search", fake_web_search),
    ):
        await finding_enricher_agent.enrich_finding(_finding(), _prep(), [])

    # decision passed package_name="other-pkg" but the finding is "left-pad"
    assert received["package_name"] == "left-pad"


@pytest.mark.asyncio
async def test_enrich_finding_self_corrects_then_passes():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(side_effect=[_reject_verdict("add evidence"), _ok_verdict()])

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is True
    assert tools_used == ["verification_feedback"]
    assert critic.await_count == 2


@pytest.mark.asyncio
async def test_enrich_finding_marks_untrusted_when_budget_exhausted():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_reject_verdict("business_impact is not grounded"))

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "_MAX_ITERATIONS", 2),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is False
    assert draft.observation == "business_impact is not grounded"
    assert draft.dep_name == "left-pad"  # kept, not dropped


@pytest.mark.asyncio
async def test_enrich_finding_critic_failure_degrades_to_trusted():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(side_effect=RuntimeError("critic down"))

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, tools_used = await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), []
        )

    assert draft.trust is True
    assert draft.observation == ""


@pytest.mark.asyncio
async def test_enrich_finding_excludes_flagged_alternatives_from_prompt():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    seen_system: dict = {}

    async def _ainvoke(messages):
        seen_system["content"] = messages[0]["content"]
        return _finalize()

    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=_ainvoke
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        await finding_enricher_agent.enrich_finding(
            _finding(), _prep(), ["left-pad", "zod", "class-transformer"]
        )

    assert "zod" in seen_system["content"]
    assert "class-transformer" in seen_system["content"]


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_finding_enricher_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.report.agents.finding_enricher_agent'`

- [ ] **Step 3: Implement**

Create `apps/backend/src/main_graph/subgraphs/report/agents/finding_enricher_agent.py`:

```python
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import textwrap
import time
import uuid
from typing import cast

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

logger = logging.getLogger(__name__)

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

_SYSTEM = textwrap.dedent("""\
    You are a technical report writer enriching ONE dependency risk finding
    with grounded evidence. You may only investigate the package below —
    every tool call you make is forced server-side to target it regardless of
    what you pass, so there is no benefit in naming another package.

    Finding to enrich:
    - package: {dep_name}
    - severity: {severity}
    - description: {description}

    Available tools:
    {tool_descriptions}

    When you have enough evidence, set finalize=true and populate `finding`
    with a complete ReportFinding:
    - recommendation: actionable fix
    - alternatives: ONLY packages backed by a web_search result; NEVER include
      any of these already-flagged packages: {excluded_alternatives}
    - business_impact: derived from blast_radius/code_impact data if present
      (affected_file_count, isolated_to_tests_or_scripts, or the business
      capability the affected code implements). If neither tool returned
      anything, say the business impact could not be determined — never
      invent file counts or guess.
    - evidence: only cite results that actually discuss this finding's own
      reason, never a generic tutorial that happens to mention the package.
    - affected_files: from blast_radius/code_impact output, if any.

    After {max_iter} iterations, set finalize=true regardless of coverage.
    """).strip()


def _build_tool_map(prep: PrepResult, container) -> dict:
    tools: dict = {"web_search": web_search}
    if prep.codegraph_ready:
        tools["blast_radius"] = make_blast_radius_tool(
            prep.repo_path, container, settings.codegraph_docker_image
        )
    if prep.vector_store_id:
        tools["code_impact"] = make_code_impact_tool(prep.vector_store_id)
    return tools


def _format_tools(tool_map: dict) -> str:
    return "\n".join(f"- {_TOOL_DESCRIPTIONS[name]}" for name in tool_map)


def _format_results(results: list[ToolResult]) -> str:
    if not results:
        return "No results yet."
    parts = []
    for tr in results[-10:]:
        val = (
            f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        )
        parts.append(f"[{tr.tool}] → {val}")
    return "\n\n".join(parts)


async def _run_tool(tc: ToolCall, tool_map: dict, dep_name: str) -> ToolResult:
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
    sig = inspect.signature(fn.func if hasattr(fn, "func") else fn)
    if "package_name" in sig.parameters:
        # Force-injected: this subagent can only ever fetch evidence for its
        # own finding's package, regardless of what the LLM passed.
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
        logger.warning("finding_enricher: tool %s failed: %s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=kwargs,
            output={},
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _feedback_result(feedback: str) -> ToolResult:
    return ToolResult(
        id=str(uuid.uuid4()),
        tool="verification_feedback",
        args={},
        output={"feedback": feedback},
        error=None,
        duration_ms=0,
    )


def _grounded_blast_radius(tool_results: list[ToolResult]) -> BlastRadiusSummary | None:
    for tr in tool_results:
        if tr.tool == "blast_radius" and not tr.error and tr.output.get("available"):
            return BlastRadiusSummary(
                **{k: v for k, v in tr.output.items() if k in _BLAST_RADIUS_FIELDS}
            )
    return None


def _fallback_finding(finding: FindingNote) -> ReportFinding:
    return ReportFinding(
        dep_name=finding.dep_name,
        severity=finding.severity,
        description=finding.description,
        recommendation="Review manually",
    )


async def enrich_finding(
    finding: FindingNote,
    prep: PrepResult,
    all_flagged_dep_names: list[str],
    container=None,
) -> tuple[ReportFinding, list[str]]:
    tool_map = _build_tool_map(prep, container)
    tool_results: list[ToolResult] = []
    draft: ReportFinding | None = None
    excluded = (
        ", ".join(n for n in all_flagged_dep_names if n != finding.dep_name) or "none"
    )

    structured = _llm.with_structured_output(
        FindingEnrichmentDecision, method="function_calling"
    )

    for iteration in range(_MAX_ITERATIONS):
        system = _SYSTEM.format(
            dep_name=finding.dep_name,
            severity=finding.severity,
            description=finding.description,
            tool_descriptions=_format_tools(tool_map),
            excluded_alternatives=excluded,
            max_iter=_MAX_ITERATIONS,
        )
        prompt = (
            f"Tool results so far:\n{_format_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        try:
            decision = cast(
                FindingEnrichmentDecision,
                await structured.ainvoke(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]
                ),
            )
        except Exception as exc:
            logger.warning(
                "finding_enricher: structured decision failed, retrying: %s", exc
            )
            continue

        last = iteration == _MAX_ITERATIONS - 1
        if decision.finalize or last:
            draft = decision.finding or _fallback_finding(finding)
            grounded = _grounded_blast_radius(tool_results)
            if grounded is not None:
                draft.blast_radius = grounded
                draft.affected_files = grounded.affected_files
            try:
                verdict = await critique_report_finding(finding, draft, tool_results)
            except Exception as exc:
                logger.warning(
                    "finding_enricher: critique failed, accepting draft: %s", exc
                )
                draft.trust = True
                draft.observation = ""
                break
            if verdict.ok:
                draft.trust = True
                draft.observation = ""
                break
            if last:
                draft.trust = False
                draft.observation = verdict.feedback
                break
            tool_results.append(_feedback_result(verdict.feedback))
            continue

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[
                    _run_tool(tc, tool_map, finding.dep_name)
                    for tc in decision.tool_calls
                ]
            )
            tool_results.extend(new_results)

    assert draft is not None
    return draft, [tr.tool for tr in tool_results]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_finding_enricher_agent.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/agents/finding_enricher_agent.py tests/unit/test_finding_enricher_agent.py
git commit -m "feat: add finding_enricher_agent, per-finding ReAct + self-critique loop"
```

---

### Task 5: `report/nodes/report_intake.py` — fetch + severity-filter, no LLM

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/report/nodes/report_intake.py`

**Interfaces:**
- Consumes: `dao.get_analysis` (existing `ResultDAO`), `filter_by_min_severity` (`src/utils/severity.py`, existing), `settings.risk_min_severity` (existing).
- Produces: writes `findings_to_enrich: list[dict]`, `all_flagged_dep_names: list[str]` into `ReportState`. Consumed by Task 8 (`graph.py`'s `_dispatch_findings`) and Task 6 (`finding_enricher` node reads `all_flagged_dep_names`).

No standalone unit test — `report_intake` needs a live `ResultDAO`/MongoDB, so it's covered by Task 9's subgraph integration tests (severity-filtering assertions).

- [ ] **Step 1: Implement**

Create `apps/backend/src/main_graph/subgraphs/report/nodes/report_intake.py`:

```python
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.state import ReportState
from src.models.results import AnalysisResult
from src.utils.config import settings
from src.utils.severity import filter_by_min_severity

logger = logging.getLogger(__name__)


async def report_intake(state: ReportState, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    findings = filter_by_min_severity(analysis.findings, settings.risk_min_severity)
    dep_names = [f.dep_name for f in findings]

    logger.info(
        "report_intake: findings_to_enrich=%d (of %d total)",
        len(findings),
        len(analysis.findings),
    )
    return {
        "findings_to_enrich": [f.model_dump() for f in findings],
        "all_flagged_dep_names": dep_names,
    }
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd apps/backend && uv run python -c "from src.main_graph.subgraphs.report.nodes.report_intake import report_intake; print(report_intake.__name__)"`
Expected: prints `report_intake`, no error

- [ ] **Step 3: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/nodes/report_intake.py
git commit -m "feat: add report_intake node (severity filter, no LLM)"
```

---

### Task 6: `report/nodes/finding_enricher.py` — node wrapper

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/report/nodes/finding_enricher.py`

**Interfaces:**
- Consumes: `enrich_finding` (Task 4), `state["current_finding"]`/`state["all_flagged_dep_names"]` (Task 2), `dao.get_prep` (existing).
- Produces: appends one `ReportFinding.model_dump()` dict to `enriched_findings`. Consumed by Task 8 (`graph.py`).

- [ ] **Step 1: Implement**

Create `apps/backend/src/main_graph/subgraphs/report/nodes/finding_enricher.py`:

```python
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.agents.finding_enricher_agent import (
    enrich_finding,
)
from src.main_graph.subgraphs.report.state import ReportState
from src.models.conductor import FindingNote
from src.models.results import PrepResult

logger = logging.getLogger(__name__)


async def finding_enricher(state: ReportState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep: PrepResult = await dao.get_prep(state["prep_result_id"])
    finding = FindingNote(**state["current_finding"])
    all_flagged_dep_names = state.get("all_flagged_dep_names") or []

    logger.info(
        "finding_enricher: dep_name=%s severity=%s", finding.dep_name, finding.severity
    )

    draft, tools_used = await enrich_finding(
        finding, prep, all_flagged_dep_names, container
    )

    logger.info(
        "finding_enricher: dep_name=%s trust=%s tools_used=%s",
        finding.dep_name,
        draft.trust,
        tools_used,
    )
    return {"enriched_findings": [draft.model_dump()]}
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd apps/backend && uv run python -c "from src.main_graph.subgraphs.report.nodes.finding_enricher import finding_enricher; print(finding_enricher.__name__)"`
Expected: prints `finding_enricher`, no error

- [ ] **Step 3: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/nodes/finding_enricher.py
git commit -m "feat: add finding_enricher node wrapper"
```

---

### Task 7: `report/nodes/enrichment_collector.py` — no-op fan-in

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/report/nodes/enrichment_collector.py`

**Interfaces:**
- Produces: no-op node, exists only as a `Send` fan-in join point for Task 8's graph wiring.

- [ ] **Step 1: Implement**

Create `apps/backend/src/main_graph/subgraphs/report/nodes/enrichment_collector.py`:

```python
from __future__ import annotations

from src.main_graph.subgraphs.report.state import ReportState


async def enrichment_collector(state: ReportState) -> dict:
    """No-op fan-in node — triggers re-entry after all finding_enricher
    branches finish."""
    return {}
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd apps/backend && uv run python -c "from src.main_graph.subgraphs.report.nodes.enrichment_collector import enrichment_collector; print(enrichment_collector.__name__)"`
Expected: prints `enrichment_collector`, no error

- [ ] **Step 3: Commit**

```bash
cd apps/backend && git add src/main_graph/subgraphs/report/nodes/enrichment_collector.py
git commit -m "feat: add enrichment_collector no-op fan-in node"
```

---

### Task 8: `report_synthesizer.py` + `graph.py` rewrite — delete `report_conductor.py`/`report_tool_runner.py`/`save_report_result.py`, remove `ReportConductorDecision`, fix `run_subgraph.py`

This task is atomic (single commit) because `report_conductor.py`, `report_tool_runner.py`, `save_report_result.py`, and the old `graph.py` are mutually coupled — `graph.py` is the only importer of all three, so they must be deleted and rewritten together, or the repo is left in a non-importable state between commits.

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/report/nodes/report_synthesizer.py`
- Modify: `apps/backend/src/main_graph/subgraphs/report/graph.py`
- Modify: `apps/backend/src/models/results.py` (remove `ReportConductorDecision` — its only callers, `report_conductor.py`/`report_tool_runner.py`, are deleted in this task)
- Modify: `apps/backend/tests/unit/test_report_routing.py`
- Modify: `apps/backend/scripts/run_subgraph.py` (remove obsolete `"tool_results": []` from the report subgraph's initial state)
- Delete: `apps/backend/src/main_graph/subgraphs/report/nodes/save_report_result.py`
- Delete: `apps/backend/src/main_graph/subgraphs/report/nodes/report_conductor.py`
- Delete: `apps/backend/src/main_graph/subgraphs/report/nodes/report_tool_runner.py`
- Delete: `apps/backend/tests/unit/subgraphs/report/test_save_report_result.py` (its two remaining-relevant pure-function tests, `_grounded_blast_radius`, already migrated into Task 4's test file; `_group_enrichment_by_dep` no longer exists)

**Interfaces:**
- Consumes: `state["enriched_findings"]` (Task 2), `dao.save_report` (existing), `report_intake` (Task 5), `finding_enricher` (Task 6), `enrichment_collector` (Task 7).
- Produces: `report_result_id` written into `ReportState`; `_dispatch_findings(state: ReportState) -> str | list[Send]`, `build_report_subgraph()`, `report_subgraph`. Consumed by Task 9 (subgraph integration tests) and `scripts/run_subgraph.py`.

- [ ] **Step 1: Write the failing routing tests**

Replace the full contents of `apps/backend/tests/unit/test_report_routing.py`:

```python
from __future__ import annotations

from langgraph.types import Send

from src.main_graph.subgraphs.report.graph import _dispatch_findings


def test_empty_findings_goes_to_synthesizer():
    assert _dispatch_findings({"findings_to_enrich": []}) == "save_report_result"


def test_missing_findings_key_goes_to_synthesizer():
    assert _dispatch_findings({}) == "save_report_result"


def test_findings_fan_out_via_send():
    finding = {
        "dep_name": "lodash",
        "severity": "high",
        "description": "CVE",
        "evidence": [],
    }
    result = _dispatch_findings({"findings_to_enrich": [finding]})
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], Send)
    assert result[0].node == "finding_enricher"


def test_multiple_findings_produce_multiple_sends():
    findings = [
        {"dep_name": "lodash", "severity": "high", "description": "d1", "evidence": []},
        {
            "dep_name": "axios",
            "severity": "medium",
            "description": "d2",
            "evidence": [],
        },
    ]
    result = _dispatch_findings({"findings_to_enrich": findings})
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(s, Send) and s.node == "finding_enricher" for s in result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_report_routing.py -v`
Expected: FAIL — `ImportError: cannot import name '_dispatch_findings'`

- [ ] **Step 3: Implement `report_synthesizer.py`**

Create `apps/backend/src/main_graph/subgraphs/report/nodes/report_synthesizer.py`:

```python
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.state import ReportState
from src.models.results import ReportFinding, ReportResult
from src.utils.llm import Model, get_llm, parse_llm_json
from src.utils.severity import SEVERITY_ORDER

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a technical report writer. You are given a list of already-vetted
dependency risk findings — each already carries its own recommendation,
alternatives, business_impact, and evidence, produced and critiqued by an
independent per-finding agent. Do not alter, add, or remove any finding;
write only the report-level narrative.

Output ONLY valid JSON:
{
  "executive_summary": "<2-4 sentence summary across all findings>",
  "recommendations": ["<top-level recommendation>"]
}
"""


def _format_findings(findings: list[ReportFinding]) -> str:
    if not findings:
        return "No findings."
    parts = []
    for f in findings:
        trust_note = "" if f.trust else f" [UNTRUSTED: {f.observation}]"
        parts.append(f"- [{f.severity.upper()}] {f.dep_name}: {f.description}{trust_note}")
    return "\n".join(parts)


async def report_synthesizer(state: ReportState, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    findings = [ReportFinding(**f) for f in (state.get("enriched_findings") or [])]

    user_prompt = f"Concern: {state['concern']}\n\nFindings:\n{_format_findings(findings)}"

    try:
        response = await _llm.ainvoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_prompt},
            ]
        )
        content = response.content if isinstance(response.content, str) else ""
        data = parse_llm_json(content)
    except Exception as exc:
        logger.warning("report_synthesizer: narrative generation failed: %s", exc)
        data = {"executive_summary": "", "recommendations": []}

    overall = max(
        (f.severity for f in findings),
        key=lambda s: SEVERITY_ORDER.get(s, 0),
        default="none",
    )

    result = ReportResult(
        job_id=state["job_id"],
        concern=state["concern"],
        executive_summary=data.get("executive_summary", ""),
        overall_risk_level=overall,
        findings=findings,
        recommendations=data.get("recommendations", []),
    )
    report_result_id = await dao.save_report(result)
    logger.info(
        "report_synthesizer: saved report_result_id=%s findings=%d",
        report_result_id,
        len(findings),
    )
    return {"report_result_id": report_result_id}
```

- [ ] **Step 4: Rewrite `graph.py`**

Replace the full contents of `apps/backend/src/main_graph/subgraphs/report/graph.py`:

```python
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.main_graph.subgraphs.report.nodes.enrichment_collector import (
    enrichment_collector,
)
from src.main_graph.subgraphs.report.nodes.finding_enricher import finding_enricher
from src.main_graph.subgraphs.report.nodes.report_intake import report_intake
from src.main_graph.subgraphs.report.nodes.report_synthesizer import (
    report_synthesizer,
)
from src.main_graph.subgraphs.report.state import ReportState


def _dispatch_findings(state: ReportState):
    """
    Route from report_intake.

    Returns a list[Send] to fan out to parallel finding_enricher invocations,
    or the string "save_report_result" to finalize with no findings.

    Note: Send-based fan-out must happen from a conditional EDGE function,
    not from a node — LangGraph 1.x does not support list[Send] node returns.
    """
    findings = state.get("findings_to_enrich") or []
    if not findings:
        return "save_report_result"
    return [
        Send("finding_enricher", {**state, "current_finding": f}) for f in findings
    ]


def build_report_subgraph():
    builder = StateGraph(ReportState)

    builder.add_node("report_intake", report_intake)
    builder.add_node("finding_enricher", finding_enricher)
    builder.add_node("enrichment_collector", enrichment_collector)
    builder.add_node("save_report_result", report_synthesizer)

    builder.add_edge(START, "report_intake")
    builder.add_conditional_edges("report_intake", _dispatch_findings)
    builder.add_edge("finding_enricher", "enrichment_collector")
    builder.add_edge("enrichment_collector", "save_report_result")
    builder.add_edge("save_report_result", END)

    return builder.compile()


report_subgraph = build_report_subgraph()
```

Note: the graph node is kept named `"save_report_result"` (mapped to the `report_synthesizer` function) so `_dispatch_findings`'s empty-findings return value and any external tooling keyed on that node name keep working — only the function/file identity changed, not the node name in the compiled graph.

- [ ] **Step 5: Delete the superseded files**

```bash
cd apps/backend
git rm src/main_graph/subgraphs/report/nodes/report_conductor.py
git rm src/main_graph/subgraphs/report/nodes/report_tool_runner.py
git rm src/main_graph/subgraphs/report/nodes/save_report_result.py
git rm tests/unit/subgraphs/report/test_save_report_result.py
```

- [ ] **Step 6: Remove `ReportConductorDecision` from `models/results.py`**

`report_conductor.py`/`report_tool_runner.py` were its only callers and are now deleted. In `apps/backend/src/models/results.py`, remove:

```python
class ReportConductorDecision(BaseModel):
    tool_calls: list[ToolCall]
    finalize: bool = False
    reasoning: str
```

(the class added back in Task 1, immediately below `FindingEnrichmentDecision` — delete it, leaving `FindingEnrichmentDecision` as the last class in that region of the file before `ReportResult`).

- [ ] **Step 7: Fix `scripts/run_subgraph.py`**

In `apps/backend/scripts/run_subgraph.py`, in `_run_report`, remove the now-obsolete `"tool_results": []` line from the initial state dict passed to `graph.ainvoke`:

```python
    graph = build_report_subgraph()
    result = await graph.ainvoke(
        {
            "job_id": job_id,
            "concern": args.concern,
            "prep_result_id": args.prep_result_id,
            "analysis_result_id": args.analysis_result_id,
        },
        config=config,
    )
```

- [ ] **Step 8: Verify `report_synthesizer` imports cleanly and routing tests pass**

Run: `cd apps/backend && uv run python -c "from src.main_graph.subgraphs.report.nodes.report_synthesizer import report_synthesizer; print(report_synthesizer.__name__)"`
Expected: prints `report_synthesizer`, no error

Run: `cd apps/backend && uv run pytest tests/unit/test_report_routing.py tests/unit/test_result_models.py -v`
Expected: PASS (all tests — routing tests plus the Task 1 model tests, confirming `ReportConductorDecision`'s removal didn't break anything since nothing else imports it)

- [ ] **Step 9: Commit**

```bash
cd apps/backend && git add -A
git commit -m "feat: rewrite report graph for per-finding Send fan-out; remove report_conductor/report_tool_runner/save_report_result and ReportConductorDecision"
```

---

### Task 9: `tests/subgraphs/test_report_subgraph.py` full rewrite (integration)

**Files:**
- Modify: `apps/backend/tests/subgraphs/test_report_subgraph.py` (full rewrite)

**Interfaces:**
- Consumes: `build_report_subgraph` (Task 8), `finding_enricher_agent` module (Task 4, patched at `_llm`/`critique_report_finding`), `report_synthesizer` module (Task 8, patched at `_llm`), existing `subgraph_config`/`result_dao` fixtures from `tests/subgraphs/conftest.py` (unchanged).

- [ ] **Step 1: Replace the full contents of the test file**

Replace `apps/backend/tests/subgraphs/test_report_subgraph.py`:

```python
"""Blackbox integration test for the report subgraph.

What is real:
- report_intake (severity filtering via MongoDB-backed AnalysisResult)
- report_synthesizer (MongoDB persistence via testcontainer)
- ReportResult construction, overall_risk_level derivation (pure Python)

What is mocked:
- finding_enricher_agent._llm (controlled per-finding decisions, matched to
  the finding by dep_name since parallel Send branches run in nondeterministic
  order)
- finding_enricher_agent.critique_report_finding (controlled verdicts)
- report_synthesizer._llm (returns canned executive_summary/recommendations)
- AnalysisResult is seeded directly into MongoDB (no analysis run needed)
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.report.agents import finding_enricher_agent
from src.main_graph.subgraphs.report.agents.critique import FindingVerdict
from src.main_graph.subgraphs.report.graph import build_report_subgraph
from src.models.conductor import FindingNote
from src.models.results import (
    AnalysisResult,
    FindingEnrichmentDecision,
    PrepResult,
    ReportFinding,
)


def _seed_analysis(
    job_id: str, findings: list[FindingNote] | None = None
) -> AnalysisResult:
    if findings is None:
        findings = [
            FindingNote(
                dep_name="lodash",
                severity="high",
                description="CVE-2021-23337: prototype pollution",
                evidence=[],
            ),
            FindingNote(
                dep_name="axios",
                severity="medium",
                description="SSRF risk in axios < 1.7",
                evidence=[],
            ),
        ]
    return AnalysisResult(
        job_id=job_id,
        concern="security vulnerabilities",
        findings=findings,
        evidence_bundle_ids=["bundle-1", "bundle-2"],
        iteration_count=2,
    )


def _finalize(finding: ReportFinding) -> FindingEnrichmentDecision:
    return FindingEnrichmentDecision(
        tool_calls=[], finding=finding, finalize=True, reasoning="enriched"
    )


def _ok_verdict() -> FindingVerdict:
    return FindingVerdict(ok=True, feedback="", calibrated_confidence=0.9)


def _make_enricher_llm(decisions_by_dep: dict[str, FindingEnrichmentDecision]):
    async def _ainvoke(messages):
        system = messages[0]["content"]
        for dep, decision in decisions_by_dep.items():
            if f"package: {dep}" in system:
                return decision
        raise AssertionError(f"no mocked decision matched system prompt: {system[:200]}")

    chain = MagicMock()
    chain.ainvoke = AsyncMock(side_effect=_ainvoke)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=chain)
    return llm


def _make_synthesizer_llm(payload: dict):
    response = MagicMock()
    response.content = json.dumps(payload)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=response)
    return llm


@pytest.mark.asyncio
async def test_report_produces_report_result_with_trusted_findings(
    subgraph_config, result_dao
):
    job_id = f"rep-{uuid.uuid4().hex[:8]}"
    analysis = _seed_analysis(job_id)
    await result_dao.save_analysis(analysis)

    decisions = {
        "lodash": _finalize(
            ReportFinding(
                dep_name="lodash",
                severity="high",
                description="CVE-2021-23337: prototype pollution",
                recommendation="Upgrade to lodash >= 4.17.21",
            )
        ),
        "axios": _finalize(
            ReportFinding(
                dep_name="axios",
                severity="medium",
                description="SSRF risk in axios < 1.7",
                recommendation="Upgrade to axios >= 1.7",
            )
        ),
    }
    synth_payload = {
        "executive_summary": "lodash and axios both need upgrades.",
        "recommendations": ["Upgrade lodash and axios"],
    }

    with (
        patch.object(finding_enricher_agent, "_llm", _make_enricher_llm(decisions)),
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
                "concern": "security vulnerabilities",
                "prep_result_id": "unused-for-report",
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert report.job_id == job_id
    assert len(report.findings) == 2
    assert all(f.trust for f in report.findings)
    assert report.overall_risk_level == "high"
    assert report.executive_summary


@pytest.mark.asyncio
async def test_report_with_empty_findings(subgraph_config, result_dao):
    job_id = f"rep-{uuid.uuid4().hex[:8]}"
    analysis = _seed_analysis(job_id, findings=[])
    await result_dao.save_analysis(analysis)

    synth_payload = {
        "executive_summary": "No significant risks found in this project.",
        "recommendations": [],
    }

    with patch(
        "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
        _make_synthesizer_llm(synth_payload),
    ):
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "general review",
                "prep_result_id": "unused",
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert report.overall_risk_level == "none"
    assert report.findings == []


@pytest.mark.asyncio
async def test_report_drops_low_severity_findings_before_enrichment(
    subgraph_config, result_dao
):
    """risk_min_severity="high" filters out the medium finding in
    report_intake, before it ever reaches a finding_enricher — only the
    high finding's decision needs to be mocked."""
    job_id = f"rep-{uuid.uuid4().hex[:8]}"
    findings = [
        FindingNote(dep_name="lodash", severity="high", description="high risk", evidence=[]),
        FindingNote(
            dep_name="axios", severity="medium", description="medium risk", evidence=[]
        ),
    ]
    analysis = _seed_analysis(job_id, findings=findings)
    await result_dao.save_analysis(analysis)

    decisions = {
        "lodash": _finalize(
            ReportFinding(
                dep_name="lodash",
                severity="high",
                description="high risk",
                recommendation="Upgrade lodash",
            )
        )
    }
    synth_payload = {"executive_summary": "lodash is high risk.", "recommendations": []}

    with (
        patch.object(finding_enricher_agent, "_llm", _make_enricher_llm(decisions)),
        patch.object(
            finding_enricher_agent,
            "critique_report_finding",
            AsyncMock(return_value=_ok_verdict()),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_synthesizer._llm",
            _make_synthesizer_llm(synth_payload),
        ),
        patch(
            "src.main_graph.subgraphs.report.nodes.report_intake.settings"
        ) as mock_settings,
    ):
        mock_settings.risk_min_severity = "high"
        graph = build_report_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "security vulnerabilities",
                "prep_result_id": "unused-for-report",
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert len(report.findings) == 1
    assert report.findings[0].dep_name == "lodash"


@pytest.mark.asyncio
async def test_report_keeps_untrusted_finding_instead_of_dropping(
    subgraph_config, result_dao
):
    """A finding whose evidence fails critique stays in the report, flagged
    trust=False with the critique feedback as observation — never dropped."""
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

    decisions = {
        "left-pad": _finalize(
            ReportFinding(
                dep_name="left-pad",
                severity="high",
                description="GPL-incompatible copyleft dependency",
                recommendation="Remove or replace left-pad",
            )
        )
    }
    synth_payload = {
        "executive_summary": "left-pad is GPL-incompatible.",
        "recommendations": [],
    }

    with (
        patch.object(finding_enricher_agent, "_llm", _make_enricher_llm(decisions)),
        patch.object(finding_enricher_agent, "_MAX_ITERATIONS", 1),
        patch.object(
            finding_enricher_agent,
            "critique_report_finding",
            AsyncMock(
                return_value=FindingVerdict(
                    ok=False,
                    feedback="business_impact is not grounded in tool output",
                    calibrated_confidence=0.2,
                )
            ),
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
                "prep_result_id": "unused-for-report",
                "analysis_result_id": analysis.id,
            },
            config=subgraph_config,
        )

    assert result.get("report_result_id")
    report = await result_dao.get_report(result["report_result_id"])
    assert len(report.findings) == 1  # kept, not dropped
    finding = report.findings[0]
    assert finding.trust is False
    assert finding.observation == "business_impact is not grounded in tool output"


@pytest.mark.asyncio
async def test_report_grounds_blast_radius_via_codegraph(subgraph_config, result_dao):
    """finding_enricher's own blast_radius tool call -> container port ->
    the resulting draft's blast_radius/affected_files come from the real
    tool output, not the LLM's placeholder text."""
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

    tool_call_decision = FindingEnrichmentDecision(
        tool_calls=[
            ToolCall(
                tool="blast_radius",
                args={"package_name": "left-pad"},
                reason="check real usage depth",
            )
        ],
        finding=None,
        finalize=False,
        reasoning="enrich with blast radius",
    )
    final_decision = _finalize(
        ReportFinding(
            dep_name="left-pad",
            severity="high",
            description="GPL-incompatible copyleft dependency",
            recommendation="Remove or replace left-pad",
            affected_files=["should-be-overwritten.js:1"],
            business_impact="Only used in a build script, never shipped.",
        )
    )

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=[tool_call_decision, final_decision]
    )
    synth_payload = {
        "executive_summary": "left-pad is GPL-incompatible but low exposure.",
        "recommendations": ["Replace left-pad with String.prototype.padStart"],
    }

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
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
    assert finding.blast_radius.affected_file_count == 1
    assert finding.blast_radius.isolated_to_tests_or_scripts is True
    # grounded from the real tool output, not the LLM's placeholder text
    assert finding.affected_files == ["scripts/build.js:1"]
```

- [ ] **Step 2: Run the subgraph tests**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_report_subgraph.py -v`
Expected: PASS (all 5 tests). Requires the MongoDB testcontainer used by `result_dao`/`subgraph_config` fixtures (same as before this refactor — no fixture changes needed).

- [ ] **Step 3: Commit**

```bash
cd apps/backend && git add tests/subgraphs/test_report_subgraph.py
git commit -m "test: rewrite report subgraph integration tests for per-finding fan-out"
```

---

### Task 10: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: PASS, zero failures. In particular confirm `tests/subgraphs/test_analysis_subgraph.py` and everything under `tests/unit/` still passes unaffected (this refactor touches only `report/`).

- [ ] **Step 2: Run lint and typecheck**

Run: `cd apps/backend && uv run ruff check .`
Expected: no errors (in particular: no unused imports left behind in `models/results.py` from removing `ReportConductorDecision`, no leftover imports of deleted `report_conductor`/`report_tool_runner`/`save_report_result` modules anywhere)

Run: `cd apps/backend && uv run mypy .`
Expected: no new type errors introduced by this refactor

- [ ] **Step 3: Grep for stale references to deleted symbols**

Run: `cd apps/backend && grep -rn "ReportConductorDecision\|report_conductor\|report_tool_runner\|save_report_result\|_group_enrichment_by_dep\|_drop_mismatched_evidence" --include="*.py" .`
Expected: no output (all references removed; the compiled graph's internal node name `"save_report_result"` string literal in `graph.py` is fine and intentional — this grep is for stale imports/symbols, so review any hits manually rather than assuming failure)

- [ ] **Step 4: Manually smoke-test via `run_subgraph.py`**

If a MongoDB instance and a prior `AnalysisResult` are available locally (see `apps/backend/docs/development-setup.md`):

Run: `cd apps/backend && uv run python scripts/run_subgraph.py report --job-id smoke-test --prep-result-id <existing-prep-id> --analysis-result-id <existing-analysis-id> --concern "security review"`
Expected: prints `[report] report_result_id = ...` followed by a `--- ReportResult ---` block with `risk_level`, `findings`, `recommendations`, and an executive summary — confirms the new graph wiring runs end-to-end against a real LLM and real MongoDB, not just mocks.

- [ ] **Step 5: Final commit (if any cleanup was needed from Steps 2–3)**

```bash
cd apps/backend && git add -A
git commit -m "chore: fix lint/typecheck findings from report subgraph refactor"
```

(Skip this step if Steps 2–3 found nothing to fix.)
