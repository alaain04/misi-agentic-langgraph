# Agent Evidence Self-Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-loop evaluator to each analysis domain agent that verifies findings against their attached evidence, lets the agent self-correct within its existing budget, and surfaces unresolved concerns to the conductor.

**Architecture:** A new `critique_findings` LLM call gates the `finalize` step inside `base_agent._react_loop`. On rejection with budget remaining, its feedback is injected as a synthetic tool result and the agent revises with tool access. On budget exhaustion, findings are kept, confidence is lowered to the critic's `calibrated_confidence`, and the critique is attached to the bundle as `verification_note` for the conductor to act on.

**Tech Stack:** Python 3.12, LangGraph, LangChain structured output, Pydantic, pytest / pytest-asyncio, `uv`.

## Global Constraints

- Package manager is `uv`. Always `uv run <cmd>`, never bare `python`/`pytest`.
- All test/run commands execute from `apps/backend/` (where `pyproject.toml` lives).
- LLM model for the critic is `Model.GPT_5_4_MINI` (same as the domain agent).
- Every new module starts with `from __future__ import annotations`.
- No defensive overengineering; a critic failure must degrade to a pass, never fail the analysis.
- Absolute path base for commands: `/Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend`.

---

## File Structure

- `src/models/results.py` — add `verification_note` to `EvidenceBundle` (Task 1).
- `src/main_graph/subgraphs/analysis/agents/critique.py` — new: `FindingsVerdict` + `critique_findings` (Task 2).
- `src/main_graph/subgraphs/analysis/agents/base_agent.py` — finalize gate in `_react_loop` (Task 3).
- `src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py` — surface note in `_format_bundles` + one prompt line (Task 4).
- Tests: `tests/unit/test_result_models.py`, `tests/unit/test_critique.py` (new), `tests/unit/test_base_agent.py`, `tests/unit/test_analysis_conductor.py` (new).

---

### Task 1: `EvidenceBundle.verification_note` field

**Files:**
- Modify: `src/models/results.py:46-53` (`EvidenceBundle`)
- Test: `tests/unit/test_result_models.py`

**Interfaces:**
- Produces: `EvidenceBundle.verification_note: str | None = None` — consumed by Task 3 (set) and Task 4 (rendered).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_result_models.py`:

```python
def test_evidence_bundle_verification_note_defaults_none():
    from src.models.results import EvidenceBundle
    b = EvidenceBundle(domain="d", hypothesis="h", findings=[], summary="s", confidence=0.5)
    assert b.verification_note is None


def test_evidence_bundle_accepts_verification_note():
    from src.models.results import EvidenceBundle
    b = EvidenceBundle(
        domain="d", hypothesis="h", findings=[], summary="s",
        confidence=0.3, verification_note="finding 1 lacks evidence",
    )
    assert b.verification_note == "finding 1 lacks evidence"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_result_models.py -k verification_note -v`
Expected: FAIL — `TypeError`/`ValidationError` on unexpected keyword `verification_note`.

- [ ] **Step 3: Add the field**

In `src/models/results.py`, in `EvidenceBundle`, after `confidence: float`:

```python
class EvidenceBundle(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: str
    hypothesis: str
    packages_to_focus: list[str] = Field(default_factory=list)
    findings: list[FindingNote]
    summary: str
    confidence: float
    verification_note: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_result_models.py -k verification_note -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/models/results.py apps/backend/tests/unit/test_result_models.py
git commit -m "feat: add verification_note to EvidenceBundle"
```

---

### Task 2: `critique_findings` evaluator

**Files:**
- Create: `src/main_graph/subgraphs/analysis/agents/critique.py`
- Test: `tests/unit/test_critique.py`

**Interfaces:**
- Consumes: `AgentDispatch` (from `src.models.results`), `FindingNote` (from `src.models.conductor`).
- Produces:
  - `class FindingsVerdict(BaseModel)` with `ok: bool`, `feedback: str`, `calibrated_confidence: float`.
  - `async def critique_findings(dispatch: AgentDispatch, findings: list[FindingNote]) -> FindingsVerdict`.
  - Module-level `_llm` (patchable in tests) and `_format_findings(findings) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_critique.py`:

```python
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="vulnerabilities", hypothesis="express has a known CVE",
        packages_to_focus=["express"], agent_type="vulnerability_agent",
    )


def test_format_findings_marks_missing_evidence():
    from src.main_graph.subgraphs.analysis.agents.critique import _format_findings

    f = FindingNote(dep_name="express", severity="high", description="CVE-123", evidence=[])
    rendered = _format_findings([f])
    assert "express" in rendered
    assert "no evidence attached" in rendered


def test_format_findings_includes_snippets():
    from src.main_graph.subgraphs.analysis.agents.critique import _format_findings

    f = FindingNote(
        dep_name="express", severity="high", description="CVE-123",
        evidence=[EvidenceRef(tool="npm_audit", url=None, log_snippet="advisory 1234 high")],
    )
    rendered = _format_findings([f])
    assert "advisory 1234 high" in rendered
    assert "npm_audit" in rendered


@pytest.mark.asyncio
async def test_critique_findings_returns_verdict():
    from src.main_graph.subgraphs.analysis.agents.critique import FindingsVerdict, critique_findings

    verdict = FindingsVerdict(ok=False, feedback="finding 1 lacks evidence", calibrated_confidence=0.2)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=verdict)

    f = FindingNote(dep_name="express", severity="high", description="CVE-123", evidence=[])
    with patch("src.main_graph.subgraphs.analysis.agents.critique._llm", mock_llm):
        result = await critique_findings(_dispatch(), [f])

    assert result.ok is False
    assert result.calibrated_confidence == 0.2
    mock_llm.with_structured_output.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_critique.py -v`
Expected: FAIL — `ModuleNotFoundError: ...agents.critique`.

- [ ] **Step 3: Write the evaluator**

Create `src/main_graph/subgraphs/analysis/agents/critique.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from src.models.conductor import FindingNote
from src.models.results import AgentDispatch
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are an evidence auditor for a dependency risk investigation.
You are given an agent's draft findings. Each finding has a claim
(dependency, severity, description) and the evidence snippets the agent
attached to justify it.

Judge ONLY whether each finding is supported by its OWN attached evidence:
- A finding with no evidence, or whose snippets do not back its claim, is unsupported.
- A severity that overstates what the evidence shows is a defect.
Do not investigate, do not add new findings, do not reason about other findings.

Output a FindingsVerdict:
- ok: true only if EVERY finding is adequately supported by its evidence.
- feedback: concrete and actionable — name the finding and what is missing or overstated.
  Empty string when ok is true.
- calibrated_confidence: 0.0-1.0 overall confidence based on evidence quality,
  independent of any self-reported score.
"""


class FindingsVerdict(BaseModel):
    ok: bool
    feedback: str
    calibrated_confidence: float


def _format_findings(findings: list[FindingNote]) -> str:
    parts = []
    for i, f in enumerate(findings, 1):
        if f.evidence:
            ev = "\n".join(f"    - [{e.tool}] {e.log_snippet}" for e in f.evidence)
        else:
            ev = "    (no evidence attached)"
        parts.append(
            f"{i}. {f.dep_name} [{f.severity}]: {f.description}\n"
            f"  evidence:\n{ev}"
        )
    return "\n\n".join(parts)


async def critique_findings(
    dispatch: AgentDispatch, findings: list[FindingNote]
) -> FindingsVerdict:
    user = (
        f"Hypothesis under test: {dispatch.hypothesis}\n\n"
        f"Findings to verify:\n{_format_findings(findings)}"
    )
    structured = _llm.with_structured_output(FindingsVerdict, method="function_calling")
    return await structured.ainvoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_critique.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/critique.py apps/backend/tests/unit/test_critique.py
git commit -m "feat: add critique_findings evidence evaluator"
```

---

### Task 3: Finalize gate in `_react_loop`

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/agents/base_agent.py:102-148` (`_react_loop`)
- Test: `tests/unit/test_base_agent.py`

**Interfaces:**
- Consumes: `critique_findings`, `FindingsVerdict` (Task 2); `verification_note` field (Task 1); `ToolResult` (already imported in `base_agent.py`).
- Produces: `EvidenceBundle` whose `confidence` is the critic's `calibrated_confidence` and whose `verification_note` carries any unresolved critique.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_base_agent.py`:

```python
def _finalize_decision(findings, confidence=0.9):
    return DomainAgentDecision(
        tool_calls=[], findings=findings,
        summary="draft", confidence=confidence, finalize=True, reasoning="done",
    )


@pytest.mark.asyncio
async def test_react_loop_self_corrects_then_passes():
    from src.main_graph.subgraphs.analysis.agents import base_agent
    from src.main_graph.subgraphs.analysis.agents.critique import FindingsVerdict

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([finding])
    )
    critic = AsyncMock(side_effect=[
        FindingsVerdict(ok=False, feedback="add evidence for express", calibrated_confidence=0.2),
        FindingsVerdict(ok=True, feedback="", calibrated_confidence=0.85),
    ])

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.85
    assert bundle.verification_note is None
    assert critic.await_count == 2  # rejected once, re-verified after self-correction
    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_react_loop_attaches_note_when_budget_exhausted():
    from src.main_graph.subgraphs.analysis.agents import base_agent
    from src.main_graph.subgraphs.analysis.agents.critique import FindingsVerdict

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([finding])
    )
    critic = AsyncMock(return_value=FindingsVerdict(
        ok=False, feedback="express finding unsupported", calibrated_confidence=0.1,
    ))

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "_MAX_ITERATIONS", 2), \
         patch.object(base_agent, "critique_findings", critic):
        bundle = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.findings == [finding]  # kept, not pruned
    assert bundle.confidence == 0.1
    assert bundle.verification_note == "express finding unsupported"


@pytest.mark.asyncio
async def test_react_loop_critic_failure_degrades_to_pass():
    from src.main_graph.subgraphs.analysis.agents import base_agent

    finding = FindingNote(dep_name="express", severity="high", description="CVE", evidence=[])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([finding], confidence=0.9)
    )
    critic = AsyncMock(side_effect=RuntimeError("critic down"))

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.9
    assert bundle.verification_note is None


@pytest.mark.asyncio
async def test_react_loop_skips_critic_when_no_findings():
    from src.main_graph.subgraphs.analysis.agents import base_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize_decision([], confidence=0.4)
    )
    critic = AsyncMock()

    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.4
    critic.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_base_agent.py -k react_loop -v`
Expected: FAIL — `critique_findings` not defined on `base_agent`, and no `verification_note` behavior.

- [ ] **Step 3: Add the import and a feedback helper**

In `src/main_graph/subgraphs/analysis/agents/base_agent.py`, add near the other imports (after line 15):

```python
from src.main_graph.subgraphs.analysis.agents.critique import critique_findings
```

Add this helper just above `_react_loop` (after `_run_tool`, before line 102):

```python
def _feedback_result(feedback: str) -> ToolResult:
    """Wrap critic feedback as a tool result so the agent sees it next iteration."""
    return ToolResult(
        id=str(uuid.uuid4()), tool="verification_feedback", args={},
        output={"feedback": feedback}, error=None, duration_ms=0,
    )
```

- [ ] **Step 4: Replace the loop body and bundle build**

Replace `_react_loop` (lines 102-148) with:

```python
async def _react_loop(
    dispatch: AgentDispatch,
    prep: PrepResult,
    tools: list,
    system_prompt: str,
) -> EvidenceBundle:
    tool_map = {(getattr(t, "name", None) or getattr(t, "__name__", repr(t))): t for t in tools}
    tool_results: list[ToolResult] = []
    decision: DomainAgentDecision | None = None
    confidence = 0.0
    note: str | None = None

    structured = _llm.with_structured_output(DomainAgentDecision, method="function_calling")

    for iteration in range(_MAX_ITERATIONS):
        prompt = (
            f"Tool results so far:\n{_format_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        system = textwrap.dedent(system_prompt).strip().format(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages=", ".join(dispatch.packages_to_focus) or "all dependencies",
            context=prep.discovery_summary[:500],
            tool_descriptions=_format_tools(tools),
            max_iter=_MAX_ITERATIONS,
        )
        decision = await structured.ainvoke([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])

        last = iteration == _MAX_ITERATIONS - 1
        if decision.finalize or last:
            if not decision.findings:
                confidence = decision.confidence
                break
            try:
                verdict = await critique_findings(dispatch, decision.findings)
            except Exception as exc:
                logger.warning("critique_findings failed, accepting draft: %s", exc)
                confidence = decision.confidence
                break
            if verdict.ok:
                confidence = verdict.calibrated_confidence
                break
            if last:
                confidence = verdict.calibrated_confidence
                note = verdict.feedback
                break
            tool_results.append(_feedback_result(verdict.feedback))
            continue

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[_run_tool(tc, tool_map, prep) for tc in decision.tool_calls]
            )
            tool_results.extend(new_results)

    return EvidenceBundle(
        domain=dispatch.domain,
        hypothesis=dispatch.hypothesis,
        packages_to_focus=dispatch.packages_to_focus,
        findings=decision.findings if decision else [],
        summary=decision.summary if decision else "No results.",
        confidence=confidence,
        verification_note=note,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_base_agent.py -v`
Expected: PASS — all react_loop tests plus the pre-existing `test_agent_run_returns_bundle_on_finalize` (its finding has no evidence, so the critic is skipped and `confidence == 0.9` still holds).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/base_agent.py apps/backend/tests/unit/test_base_agent.py
git commit -m "feat: in-loop evidence critique gate with self-correction fallback"
```

---

### Task 4: Conductor surfaces `verification_note`

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py:36-57` (`_build_system`) and `:60-73` (`_format_bundles`)
- Test: `tests/unit/test_analysis_conductor.py` (new)

**Interfaces:**
- Consumes: `EvidenceBundle.verification_note` (Task 1).
- Produces: rendered conductor input that includes flagged bundles; no signature changes.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_analysis_conductor.py`:

```python
from __future__ import annotations

from src.models.results import EvidenceBundle


def _bundle(note=None):
    return EvidenceBundle(
        domain="vulnerabilities", hypothesis="h", packages_to_focus=["express"],
        findings=[], summary="s", confidence=0.2, verification_note=note,
    )


def test_format_bundles_shows_verification_note():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import _format_bundles
    rendered = _format_bundles([_bundle(note="express finding unsupported")])
    assert "unresolved: express finding unsupported" in rendered


def test_format_bundles_omits_note_when_none():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import _format_bundles
    rendered = _format_bundles([_bundle(note=None)])
    assert "unresolved:" not in rendered


def test_system_prompt_mentions_flagged_bundles():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import _build_system
    system = _build_system(4)
    assert "unresolved" in system.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_analysis_conductor.py -v`
Expected: FAIL — note not rendered; prompt has no flagged-bundle guidance.

- [ ] **Step 3: Render the note in `_format_bundles`**

In `analysis_conductor.py`, replace the `_format_bundles` body loop so each bundle appends its note when present:

```python
def _format_bundles(bundles: list) -> str:
    if not bundles:
        return "No evidence collected yet."
    parts = []
    for b in bundles:
        packages = ", ".join(b.packages_to_focus) or "n/a"
        block = (
            f"[{b.domain}] confidence={b.confidence:.2f}\n"
            f"  hypothesis: {b.hypothesis}\n"
            f"  packages: {packages}\n"
            f"  summary: {b.summary}\n"
            f"  findings: {len(b.findings)}"
        )
        if getattr(b, "verification_note", None):
            block += f"\n  unresolved: {b.verification_note}"
        parts.append(block)
    return "\n\n".join(parts)
```

- [ ] **Step 4: Add one prompt line**

In `_build_system`, in the "Finalize when" block, add a bullet about flagged bundles. Change:

```python
Finalize when:
- All agents relevant to the concern have reported with confidence >= 0.6, OR
- Two rounds of agents produced consistent findings with no new leads, OR
- Iteration {max_iter} is reached.
"""
```

to:

```python
Finalize when:
- All agents relevant to the concern have reported with confidence >= 0.6, OR
- Two rounds of agents produced consistent findings with no new leads, OR
- Iteration {max_iter} is reached.

A bundle marked "unresolved" failed evidence verification: treat it as an open gap.
Prefer re-dispatching to close it, or discount its findings when finalizing.
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit/test_analysis_conductor.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py apps/backend/tests/unit/test_analysis_conductor.py
git commit -m "feat: surface unresolved verification notes to conductor"
```

---

### Task 5: Full suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the analysis unit + subgraph tests**

Run: `cd /Users/alain/projects/tesis/solution/apps/v3/langgraph/apps/backend && uv run pytest tests/unit tests/subgraphs/test_analysis_subgraph.py -v`
Expected: PASS. If the blackbox subgraph test asserts on bundle confidence values, confirm any change is explained by calibrated confidence and update the assertion intentionally (do not loosen it blindly).

- [ ] **Step 2: Commit any test adjustments (only if needed)**

```bash
git add apps/backend/tests
git commit -m "test: adjust analysis subgraph expectations for calibrated confidence"
```

---

## Self-Review

**Spec coverage:**
- Self-consistency evaluator (`critique_findings` vs attached snippets) → Task 2.
- In-loop finalize gate, feedback injection, exhaustion fallback, calibrated confidence → Task 3.
- `EvidenceBundle.verification_note` additive field → Task 1.
- Conductor surfaces note + prompt guidance → Task 4.
- Critic failure degrades to pass; empty findings skip critic → Task 3 (tests 3 & 4).
- Regression safety net → Task 5.
- Non-goals (ground-truth verification, cross-bundle contradictions, separate reflect node) → intentionally absent.

**Placeholder scan:** No TBD/TODO; every code and test step is complete.

**Type consistency:** `FindingsVerdict(ok, feedback, calibrated_confidence)` defined in Task 2 and used identically in Task 3. `critique_findings(dispatch, findings)` signature consistent. `verification_note` name identical across Tasks 1, 3, 4. `_feedback_result` returns `ToolResult` (already imported in `base_agent.py`), rendered by existing `_format_results`.
