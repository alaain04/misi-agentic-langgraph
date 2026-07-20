# A1 — Eliminate Spurious Finding Duplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the analysis pipeline from emitting duplicate findings when the conductor re-dispatches an agent, making the deterministic-source findings (vulnerability, license) reproducible.

**Architecture:** Two deterministic layers. Layer 1 (correctness guarantee): a pure `dedup_findings` helper collapses identical findings in `save_analysis_result` before they are persisted, upstream of report enrichment. Layer 2 (cost): a pure `drop_repeat_whole_tree_dispatches` helper in `analysis_conductor` caps the whole-tree agents (`vulnerability_agent`, `license_agent`) to one run per job, using the `agent_type`s already recorded in `state["agent_calls"]`. Both are pure functions, unit-tested without the graph, DAO, or an LLM.

**Tech Stack:** Python 3.12, LangGraph, Pydantic v2, pytest / pytest-asyncio, ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-07-20-reliability-a1-dedup-design.md`

## Global Constraints

- Package manager: `uv` (`uv run <cmd>`), never pip/bare python.
- No emoji in code, comments, or commit messages.
- Backend only — do not touch `apps/frontend`.
- Deterministic enforcement over prompt-only: the fixes are in code; do not rely on prompt edits.
- Preserve genuinely distinct findings on the same `dep_name` — dedup only collapses byte-identical duplicates, never merges across distinct issues.
- Whole-tree agents are exactly `vulnerability_agent` and `license_agent` (registry keys in `src/main_graph/subgraphs/analysis/agents/registry.py`).
- Before claiming done: run `uv run pytest`, `uv run ruff check .`, `uv run mypy src` from `apps/backend` and show output.
- All commands below run from `apps/backend/` (i.e. `apps/v3/langgraph/apps/backend`).

---

### Task 1: `dedup_findings` — collapse identical findings at the sink

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`
- Test: `tests/unit/test_save_analysis_result.py` (create)

**Interfaces:**
- Consumes: `FindingNote` (from `src.models.conductor`) — fields `dep_name: str`, `severity: str`, `description: str`, `evidence: list`.
- Produces: `dedup_findings(findings: list[FindingNote]) -> list[FindingNote]` — order-stable, keeps first occurrence, key `(dep_name, severity, description)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_save_analysis_result.py`:

```python
from __future__ import annotations

from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import dedup_findings
from src.models.conductor import EvidenceRef, FindingNote


def _finding(dep_name: str, severity: str, description: str) -> FindingNote:
    return FindingNote(
        dep_name=dep_name,
        severity=severity,
        description=description,
        evidence=[EvidenceRef(tool="npm_audit", url=None, log_snippet="x")],
    )


def test_dedup_collapses_identical_findings():
    f = _finding("electron", "critical", "CVE-1; affected <=39.8.4")
    result = dedup_findings([f, f])
    assert len(result) == 1
    assert result[0].dep_name == "electron"


def test_dedup_preserves_distinct_findings_on_same_dep():
    # same dep, different description = two distinct issues, both kept
    a = _finding("electron", "critical", "vulnerability advisory")
    b = _finding("electron", "medium", "install script risk")
    result = dedup_findings([a, b])
    assert len(result) == 2


def test_dedup_is_order_stable_keeps_first():
    a = _finding("minimatch", "high", "ReDoS 9.0.0-9.0.6")
    b = _finding("xo", "high", "vulnerable transitive")
    dup_a = _finding("minimatch", "high", "ReDoS 9.0.0-9.0.6")
    result = dedup_findings([a, b, dup_a])
    assert [f.dep_name for f in result] == ["minimatch", "xo"]


def test_dedup_empty_list():
    assert dedup_findings([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_save_analysis_result.py -v`
Expected: FAIL with `ImportError: cannot import name 'dedup_findings'`.

- [ ] **Step 3: Add the helper and wire it in**

In `src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`, add the `FindingNote` import to the existing imports:

```python
from src.models.conductor import FindingNote
```

Add the pure helper above the `save_analysis_result` function:

```python
def dedup_findings(findings: list[FindingNote]) -> list[FindingNote]:
    """Collapse byte-identical findings that a re-dispatched agent produced.

    A whole-tree agent (npm audit, license rules) returns its full finding set
    on every dispatch, so if the conductor re-dispatches it the same findings
    appear more than once. Key on (dep_name, severity, description): identical
    duplicates collapse, while genuinely distinct issues on the same package
    (different description) are preserved. Order-stable, keeps first occurrence.
    """
    seen: set[tuple[str, str, str]] = set()
    result: list[FindingNote] = []
    for f in findings:
        key = (f.dep_name, f.severity, f.description)
        if key in seen:
            continue
        seen.add(key)
        result.append(f)
    return result
```

Wire it into `save_analysis_result`, between the flatten and the severity filter. Replace:

```python
    all_findings = [f for b in bundles for f in b.findings]
    all_findings = filter_by_min_severity(all_findings, settings.risk_min_severity)
```

with:

```python
    all_findings = [f for b in bundles for f in b.findings]
    all_findings = dedup_findings(all_findings)
    all_findings = filter_by_min_severity(all_findings, settings.risk_min_severity)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_save_analysis_result.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py tests/unit/test_save_analysis_result.py && uv run mypy src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py tests/unit/test_save_analysis_result.py
git commit -m "fix: dedup identical findings before persisting analysis result

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

### Task 2: `drop_repeat_whole_tree_dispatches` — cap whole-tree agents

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`
- Test: `tests/unit/test_analysis_conductor.py` (append)

**Interfaces:**
- Consumes: `AgentDispatch` (from `src.models.results`) — fields include `agent_type: str`; `state["agent_calls"]` — a list of `AgentCallRecord.model_dump()` dicts, each carrying `agent_type`.
- Produces: `drop_repeat_whole_tree_dispatches(dispatches: list[AgentDispatch], agent_calls: list[dict]) -> list[AgentDispatch]` — drops any `vulnerability_agent`/`license_agent` dispatch whose `agent_type` already ran (in `agent_calls`) or already appears earlier in this same `dispatches` list. Non-whole-tree dispatches pass through untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_analysis_conductor.py`:

```python
def _dispatch(agent_type: str, hypothesis: str = "h"):
    from src.models.results import AgentDispatch

    return AgentDispatch(
        domain="d", hypothesis=hypothesis, packages_to_focus=[], agent_type=agent_type
    )


def test_drop_repeat_whole_tree_dispatch_already_run():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    agent_calls = [{"agent_type": "vulnerability_agent"}]
    dispatches = [_dispatch("vulnerability_agent"), _dispatch("maintenance_agent")]
    result = drop_repeat_whole_tree_dispatches(dispatches, agent_calls)
    assert [d.agent_type for d in result] == ["maintenance_agent"]


def test_drop_repeat_whole_tree_dispatch_same_round_duplicate():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    dispatches = [
        _dispatch("license_agent", "angle A"),
        _dispatch("license_agent", "angle B"),
    ]
    result = drop_repeat_whole_tree_dispatches(dispatches, [])
    assert len(result) == 1
    assert result[0].agent_type == "license_agent"


def test_does_not_cap_package_scoped_agent():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    agent_calls = [{"agent_type": "maintenance_agent"}]
    dispatches = [_dispatch("maintenance_agent")]
    result = drop_repeat_whole_tree_dispatches(dispatches, agent_calls)
    assert len(result) == 1  # package-scoped agents are not capped here


def test_keeps_novel_whole_tree_dispatch():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import (
        drop_repeat_whole_tree_dispatches,
    )

    result = drop_repeat_whole_tree_dispatches([_dispatch("vulnerability_agent")], [])
    assert len(result) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_analysis_conductor.py -k "whole_tree or package_scoped" -v`
Expected: FAIL with `ImportError: cannot import name 'drop_repeat_whole_tree_dispatches'`.

- [ ] **Step 3: Add the helper and wire it in**

In `src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`, add `AgentDispatch` to the existing model import:

```python
from src.models.results import AgentDispatch, AnalysisConductorDecision, PrepResult
```

Add a module-level constant near `_MAX_ITERATIONS`:

```python
_WHOLE_TREE_AGENTS = {"vulnerability_agent", "license_agent"}
```

Add the pure helper (place it above `analysis_conductor`, e.g. after `_format_bundles`):

```python
def drop_repeat_whole_tree_dispatches(
    dispatches: list[AgentDispatch], agent_calls: list[dict]
) -> list[AgentDispatch]:
    """Cap whole-tree agents (vulnerability, license) to one run per job.

    These agents scan the ENTIRE dependency tree in a single run, so a second
    dispatch adds zero coverage and only duplicates work, cost, and findings.
    The conductor prompt already asks for this but does not enforce it; this is
    the deterministic enforcement. Drops a whole-tree dispatch whose agent_type
    already ran (recorded in agent_calls) or already appears earlier in this
    same dispatch list. Package-scoped agents pass through untouched.
    """
    already_run = {c.get("agent_type") for c in agent_calls}
    seen_this_round: set[str] = set()
    result: list[AgentDispatch] = []
    for d in dispatches:
        if d.agent_type in _WHOLE_TREE_AGENTS:
            if d.agent_type in already_run or d.agent_type in seen_this_round:
                continue
            seen_this_round.add(d.agent_type)
        result.append(d)
    return result
```

Wire it into `analysis_conductor`, immediately after the `decision = cast(...)` block and before the `if iteration >= _MAX_ITERATIONS:` block:

```python
    filtered = drop_repeat_whole_tree_dispatches(
        decision.dispatches, state.get("agent_calls") or []
    )
    if len(filtered) != len(decision.dispatches):
        decision = decision.model_copy(update={"dispatches": filtered})

    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_analysis_conductor.py -v`
Expected: PASS (all, including the 4 new tests and the pre-existing ones).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py tests/unit/test_analysis_conductor.py && uv run mypy src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py tests/unit/test_analysis_conductor.py
git commit -m "fix: cap whole-tree agents to one dispatch per job in conductor

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

### Task 3: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `uv run pytest`
Expected: all pass, no regressions (in particular `tests/unit/test_analysis_conductor.py` and the analysis/report subgraph tests).

- [ ] **Step 2: Lint and type-check the whole backend**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 3: Optional live confirmation**

If a backend + MongoDB + Docker are available, run `chalk/chalk` through the e2e flow (see `apps/backend/docs/e2e-test-catalog.md`, test 1.1) and confirm the finding count equals the unique count with no doubling across two runs. The deterministic unit tests are the real gate; this is a live confirmation only.

- [ ] **Step 4: Commit any residual fixes**

```bash
git add -A
git commit -m "test: verify A1 dedup across backend suite

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

## Self-Review Notes

- **Spec coverage:** Layer 1 (dedup at sink) → Task 1; Layer 2 (whole-tree cap) → Task 2. The spec's "out of scope" items (general tuple dedup, frontend `dep_name` collision, LLM-judgment variance) are intentionally not in this plan.
- **Preserves distinct findings:** `dedup_findings` keys on `(dep_name, severity, description)`, so two distinct issues on the same package survive — covered by `test_dedup_preserves_distinct_findings_on_same_dep`.
- **Type consistency:** `dedup_findings(list[FindingNote]) -> list[FindingNote]` and `drop_repeat_whole_tree_dispatches(list[AgentDispatch], list[dict]) -> list[AgentDispatch]` — both pure, both used exactly as defined in their wiring steps.
- **No behavior change on single-iteration runs:** with no re-dispatch, `dedup_findings` finds no duplicates and `drop_repeat_whole_tree_dispatches` drops nothing — output is byte-identical to today.
