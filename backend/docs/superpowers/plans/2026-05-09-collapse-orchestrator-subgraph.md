# Collapse Orchestrator Subgraph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-node orchestrator subgraph with a single flat node, eliminating a duplicate state schema and fixing the planner's broken field references.

**Architecture:** The `while True` loop in `main_graph/nodes/orchestrator.py` already implements the full plan→present→interrupt→classify→replan cycle correctly. The subgraph (`main_graph/subgraphs/orchestrator/`) adds a separate `OrchestratorState`, a duplicate planner, and a `Command(goto="planner")` edge that express the same loop as graph structure — with no practical benefit. Removing the subgraph means wiring the flat `orchestrator` node directly into the main graph in place of `orchestrator_subgraph`.

**Tech Stack:** Python, LangGraph, pytest, uv

---

## File Map

| Action | Path |
|--------|------|
| Modify | `backend/src/main_graph/nodes/planner.py` |
| Modify | `backend/src/main_graph/nodes/__init__.py` |
| Modify | `backend/src/main_graph/subgraphs/__init__.py` |
| Modify | `backend/src/main_graph/graph.py` |
| Delete | `backend/src/main_graph/subgraphs/orchestrator/` (entire directory) |
| Create | `backend/tests/unit/nodes/test_planner.py` |

---

### Task 1: Fix planner field references

The planner reads `state.get("direct_dependencies", [])` and `state.get("transitive_dependencies", [])`, but those keys don't exist in `MainState`. Discovery produces `sbom_cyclonedx` with a `components` list instead.

**Files:**
- Modify: `backend/src/main_graph/nodes/planner.py`
- Create: `backend/tests/unit/nodes/__init__.py`
- Create: `backend/tests/unit/nodes/test_planner.py`

- [ ] **Step 1: Create the test file**

```python
# backend/tests/unit/nodes/test_planner.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.main_graph.nodes.planner import run_planner


def _make_state(components: list[dict], concern: str = "security", summary: str = "ok") -> dict:
    return {
        "job_id": "j1",
        "concern": concern,
        "discovery_summary": summary,
        "sbom_cyclonedx": {"components": components},
        "repo_url": "http://example.com/repo",
        "messages": [],
        "subgraph_results": [],
    }


@pytest.mark.asyncio
async def test_planner_uses_sbom_components():
    components = [{"name": "requests"}, {"name": "flask"}]
    state = _make_state(components)

    mock_response = MagicMock()
    mock_response.content = json.dumps(["vulnerabilities"])

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    call_args = mock_llm.ainvoke.call_args[0][0]
    user_msg = next(m["content"] for m in call_args if m["role"] == "user")
    assert "requests" in user_msg
    assert "flask" in user_msg


@pytest.mark.asyncio
async def test_planner_falls_back_on_bad_json():
    state = _make_state([{"name": "lodash"}])

    mock_response = MagicMock()
    mock_response.content = "not json at all"

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert isinstance(plan, list)
    assert len(plan) > 0


@pytest.mark.asyncio
async def test_planner_passes_extra_instructions():
    state = _make_state([{"name": "axios"}])

    mock_response = MagicMock()
    mock_response.content = json.dumps(["vulnerabilities"])

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state, extra_instructions="focus on licenses")

    call_args = mock_llm.ainvoke.call_args[0][0]
    user_msg = next(m["content"] for m in call_args if m["role"] == "user")
    assert "focus on licenses" in user_msg
```

- [ ] **Step 2: Create `__init__.py` for the new test package**

```bash
touch backend/tests/unit/nodes/__init__.py
```

- [ ] **Step 3: Run tests — expect failure (fields not yet fixed)**

```bash
cd backend && uv run pytest tests/unit/nodes/test_planner.py -v
```

Expected: `test_planner_uses_sbom_components` FAILS — `requests` and `flask` not found in user message because the current code reads empty `direct_dependencies` instead.

- [ ] **Step 4: Fix `planner.py` to read from `sbom_cyclonedx`**

Replace the `deps`/`transitive_deps` block in `run_planner`:

```python
async def run_planner(state: MainState, extra_instructions: str = "") -> list[str]:
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    sbom = state.get("sbom_cyclonedx", {})

    components = sbom.get("components", [])
    comp_list = ", ".join(c["name"] for c in components[:30])
    if len(components) > 30:
        comp_list += f", and {len(components) - 30} more"

    user_message = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Components ({len(components)}): {comp_list}"
    )
    if extra_instructions:
        user_message += f"\n\nAdditional instructions from the user: {extra_instructions}"

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    )

    try:
        plan = parse_llm_json(response.content or "")
        plan = [s for s in plan if s in VALID_SUBGRAPHS]
        if not plan:
            plan = _FALLBACK_PLAN
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("run_planner: failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("run_planner: selected subgraphs: %s", plan)
    return plan
```

- [ ] **Step 5: Run tests — expect all pass**

```bash
cd backend && uv run pytest tests/unit/nodes/test_planner.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/main_graph/nodes/planner.py \
        backend/tests/unit/nodes/__init__.py \
        backend/tests/unit/nodes/test_planner.py
git commit -m "fix(planner): read components from sbom_cyclonedx instead of missing fields"
```

---

### Task 2: Wire the flat orchestrator node into the main graph

Remove the subgraph import, add the flat node, update the graph builder.

**Files:**
- Modify: `backend/src/main_graph/nodes/__init__.py`
- Modify: `backend/src/main_graph/subgraphs/__init__.py`
- Modify: `backend/src/main_graph/graph.py`

- [ ] **Step 1: Export `orchestrator` from the nodes package**

In `backend/src/main_graph/nodes/__init__.py`, add the orchestrator import:

```python
from src.main_graph.nodes.execute_plan import execute_plan
from src.main_graph.nodes.execution_planner import execution_planner
from src.main_graph.nodes.orchestrator import orchestrator
from src.main_graph.nodes.stage_advance import stage_advance, stage_router
from src.main_graph.nodes.task_dispatcher import task_dispatcher

__all__ = [
    "execute_plan",
    "execution_planner",
    "orchestrator",
    "stage_advance",
    "stage_router",
    "task_dispatcher",
]
```

- [ ] **Step 2: Remove `orchestrator_subgraph` from the subgraphs package**

Replace `backend/src/main_graph/subgraphs/__init__.py` with:

```python
from src.main_graph.subgraphs.cross_analyzer import cross_analyzer_subgraph
from src.main_graph.subgraphs.discovery import discovery_subgraph
from src.main_graph.subgraphs.report_reviewer import report_reviewer_subgraph

__all__ = [
    "discovery_subgraph",
    "cross_analyzer_subgraph",
    "report_reviewer_subgraph",
]
```

- [ ] **Step 3: Update `graph.py` to use the flat node**

In `backend/src/main_graph/graph.py`, change the imports and the node registration:

```python
"""Main graph — composes all subgraphs into the full analysis pipeline."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    CROSS_ANALYZER,
    DISCOVERY,
    EXECUTE_PLAN,
    EXECUTION_PLANNER,
    ORCHESTRATOR,
    REPORT_REVIEWER,
    STAGE_ADVANCE,
)
from src.main_graph.state import MainState
from src.main_graph.subgraphs import (
    cross_analyzer_subgraph,
    discovery_subgraph,
    report_reviewer_subgraph,
)

from .nodes import (
    execute_plan,
    execution_planner,
    orchestrator,
    stage_advance,
    stage_router,
    task_dispatcher,
)

_checkpointer = InMemorySaver()

_MAX_REVIEW_ITERATIONS = 2


def _review_router(state: MainState) -> str:
    if (
        state.get("review_approved")
        or state.get("review_iterations", 0) >= _MAX_REVIEW_ITERATIONS
    ):
        return END
    return CROSS_ANALYZER


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(DISCOVERY, discovery_subgraph)
    builder.add_node(ORCHESTRATOR, orchestrator)
    builder.add_node(EXECUTION_PLANNER, execution_planner)
    builder.add_node(EXECUTE_PLAN, execute_plan)
    builder.add_node(STAGE_ADVANCE, stage_advance)
    builder.add_node(CROSS_ANALYZER, cross_analyzer_subgraph)
    builder.add_node(REPORT_REVIEWER, report_reviewer_subgraph)

    builder.add_edge(START, DISCOVERY)
    builder.add_edge(DISCOVERY, ORCHESTRATOR)
    builder.add_edge(ORCHESTRATOR, EXECUTION_PLANNER)
    builder.add_conditional_edges(EXECUTION_PLANNER, task_dispatcher, [EXECUTE_PLAN])
    builder.add_edge(EXECUTE_PLAN, STAGE_ADVANCE)
    builder.add_conditional_edges(
        STAGE_ADVANCE, stage_router, [EXECUTION_PLANNER, CROSS_ANALYZER]
    )
    builder.add_edge(CROSS_ANALYZER, REPORT_REVIEWER)
    builder.add_conditional_edges(
        REPORT_REVIEWER, _review_router, [CROSS_ANALYZER, END]
    )

    return builder.compile(checkpointer=_checkpointer)


main_graph = build_main_graph()
```

- [ ] **Step 4: Verify the graph builds without errors**

```bash
cd backend && uv run python -c "from src.main_graph.graph import main_graph; print('graph ok')"
```

Expected output: `graph ok`

- [ ] **Step 5: Commit**

```bash
git add backend/src/main_graph/nodes/__init__.py \
        backend/src/main_graph/subgraphs/__init__.py \
        backend/src/main_graph/graph.py
git commit -m "refactor(orchestrator): wire flat orchestrator node into main graph"
```

---

### Task 3: Delete the orchestrator subgraph

Now that nothing imports from it, the subgraph directory is dead code.

**Files:**
- Delete: `backend/src/main_graph/subgraphs/orchestrator/` (entire directory)

- [ ] **Step 1: Confirm nothing imports from the subgraph**

```bash
grep -r "subgraphs.orchestrator\|orchestrator_subgraph" \
  /Users/alain/projects/tesis/solution/apps/v3/langgraph/backend/src/ \
  --include="*.py"
```

Expected: no output.

- [ ] **Step 2: Delete the directory**

```bash
rm -rf backend/src/main_graph/subgraphs/orchestrator/
```

- [ ] **Step 3: Run the full unit test suite**

```bash
cd backend && uv run pytest tests/unit/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Verify the graph still builds**

```bash
cd backend && uv run python -c "from src.main_graph.graph import main_graph; print('graph ok')"
```

Expected: `graph ok`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(orchestrator): delete redundant orchestrator subgraph"
```
