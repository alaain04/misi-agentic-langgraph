# Analysis Subgraph Deepagent Swap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the internals of `analysis_subgraph` (the conductor/dispatcher/domain-agent fan-out loop) with a `deepagents`-based implementation, while keeping the subgraph's contract with the rest of `main_graph` byte-identical and reusing every existing domain-agent implementation unchanged.

**Architecture:** A root deep agent (`deepagents.create_deep_agent`) dispatches to five `CompiledSubAgent`s — one per existing `agent_type` — each a thin one-node LangGraph wrapper around today's `agent_class().run(dispatch, prep, container, cache)`. Subagent results flow back to the root via ordinary LangGraph state reducers (verified against the real library, not assumed). A deterministic coverage gate sits after the deep-agent run and guarantees every direct dependency gets evaluated by a package-scoped agent before `save_analysis_result` (unchanged) runs.

**Tech Stack:** Python, LangGraph, `deepagents==0.6.12`, Pydantic, pytest, `uv`.

## Global Constraints

- Pin `deepagents>=0.6.12,<0.7` (introspected version; behavior verified against this exact version — see spec).
- `analysis_subgraph`'s external contract must not change: in
  `{job_id, concern, prep_result_id}`, out `{analysis_result_id}`.
- `save_analysis_result.py` is not modified.
- No `execute_command`/code-execution tool reachable from the deep agent or any subagent.
- Every direct dependency must receive at least one `AgentCallRecord` from a package-scoped agent type before the subgraph finalizes (deterministic guarantee, not agent-judgment-dependent).
- `vulnerability_agent`/`license_agent` run at most once per job.
- Full backend suite (`uv run pytest`), `ruff check`, and `mypy` must be green at the end.
- Spec: `docs/superpowers/specs/2026-07-26-analysis-subgraph-deepagent-swap.md` — read it before starting; this plan implements its decisions D1–D8 and its verified deepagents mechanics.

---

## File Structure

New package `apps/backend/src/main_graph/subgraphs/analysis/deepagent/`:

- `__init__.py` — empty.
- `state.py` — `AnalysisDeepAgentState`, the root deep agent's state schema.
- `coverage.py` — `WHOLE_TREE_AGENT_TYPES`, `PACKAGE_SCOPED_AGENT_TYPES`, `compute_missing_direct_deps()`. Pure functions, no LLM.
- `subagent_wrapper.py` — `build_agent_subagent(agent_type)`, producing one `CompiledSubAgent` per registered agent type.
- `backstop.py` — `deterministic_backstop_dispatch()`, the no-LLM fallback for D5.
- `nodes.py` — `analysis_deepagent_node`, `coverage_gate`, `backstop_dispatch_node`: the three nodes that replace the old conductor/dispatcher/domain_agent/evidence_collector chain.

Modified:

- `state.py` (`apps/backend/src/main_graph/subgraphs/analysis/state.py`) — drop conductor-era fields, add coverage-loop fields.
- `graph.py` (`apps/backend/src/main_graph/subgraphs/analysis/graph.py`) — rewire `build_analysis_subgraph()`.
- `apps/backend/pyproject.toml` — add `deepagents` dependency.
- `apps/backend/tests/subgraphs/test_analysis_subgraph.py` — rewritten blackbox test.

Deleted (superseded, confirmed no other callers):

- `apps/backend/src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`
- `apps/backend/src/main_graph/subgraphs/analysis/nodes/domain_agent.py`
- `apps/backend/src/main_graph/subgraphs/analysis/nodes/evidence_collector.py`
- `apps/backend/src/main_graph/subgraphs/analysis/nodes/agent_dispatcher.py` (dead code today — its `agent_dispatcher()` function has zero callers; `graph.py`'s `_after_conductor` reimplements the same fan-out inline and is what's actually wired)
- `apps/backend/tests/unit/test_analysis_conductor.py`
- `apps/backend/tests/unit/test_analysis_routing.py`

Unchanged (verify, don't touch): `save_analysis_result.py`, `tests/unit/test_save_analysis_result.py`, every file under `agents/` (`base_agent.py`, `license_agent.py`, `vulnerability_agent.py`, `supply_chain_agent.py`, `web_research_agent.py`, `maintenance_agent.py`, `registry.py`, `critique.py`, `dependency_versions.py`).

---

### Task 1: Add `deepagents` dependency, define `AnalysisDeepAgentState`, verify state passthrough

This task's test is a smoke test for the single riskiest assumption in the whole design (verified once already in a scratch venv during spec review — this repeats it inside the real project to confirm the pinned version resolves the same way with the project's actual dependency set).

**Files:**
- Modify: `apps/backend/pyproject.toml`
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/__init__.py`
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/state.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_state_passthrough.py`

**Interfaces:**
- Produces: `AnalysisDeepAgentState` (TypedDict-like, subclass of `deepagents.DeepAgentState`) with fields `job_id: str`, `prep_result_id: str`, `bundle_ids: Annotated[list[str], operator.add]`, `agent_calls: Annotated[list[dict], operator.add]`. Task 3/4/5 all import this from `src.main_graph.subgraphs.analysis.deepagent.state`.

- [ ] **Step 1: Add the dependency**

```bash
cd apps/backend
uv add "deepagents>=0.6.12,<0.7"
```

- [ ] **Step 2: Create the package init**

`apps/backend/src/main_graph/subgraphs/analysis/deepagent/__init__.py`:
```python
```
(empty file — marks the package)

- [ ] **Step 3: Write the failing test**

`apps/backend/tests/unit/subgraphs/analysis/deepagent/test_state_passthrough.py`:
```python
"""Confirms deepagents.CompiledSubAgent state updates merge into the root
deep agent's state via ordinary LangGraph reducers (not just a summarized
ToolMessage). This is the load-bearing mechanism for D4 in
docs/superpowers/specs/2026-07-26-analysis-subgraph-deepagent-swap.md --
verified here against the pinned deepagents version inside this project,
not assumed."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Sequence

import pytest
from deepagents import CompiledSubAgent, create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.main_graph.subgraphs.analysis.deepagent.state import AnalysisDeepAgentState


class _ScriptedToolCallingChatModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel does not implement bind_tools (raises
    NotImplementedError), but deepagents calls model.bind_tools(...)
    internally. Override it as a no-op so the fake just returns its
    scripted responses regardless of the tool schema passed in."""

    def bind_tools(
        self, tools: Sequence[Any], **kwargs: Any
    ) -> "_ScriptedToolCallingChatModel":
        return self


class _EchoSubState(TypedDict):
    messages: list
    bundle_ids: Annotated[list[str], operator.add]


def _echo_node(state: _EchoSubState) -> dict:
    return {"messages": [AIMessage(content="done")], "bundle_ids": ["fake-bundle-1"]}


def _build_echo_subagent() -> CompiledSubAgent:
    graph = StateGraph(_EchoSubState)
    graph.add_node("echo", _echo_node)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    return {
        "name": "echo_agent",
        "description": "Echoes back a fixed bundle id.",
        "runnable": graph.compile(),
    }


@pytest.mark.asyncio
async def test_subagent_state_update_merges_into_root_state():
    fake_model = _ScriptedToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "run echo", "subagent_type": "echo_agent"},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content="all done"),
        ]
    )
    agent = create_deep_agent(
        model=fake_model,
        subagents=[_build_echo_subagent()],
        state_schema=AnalysisDeepAgentState,
    )
    result = await agent.ainvoke(
        {
            "messages": [HumanMessage(content="go")],
            "job_id": "job-1",
            "prep_result_id": "prep-1",
            "bundle_ids": [],
            "agent_calls": [],
        }
    )
    assert result["bundle_ids"] == ["fake-bundle-1"]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_state_passthrough.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.analysis.deepagent.state'` (file doesn't exist yet).

- [ ] **Step 5: Write `state.py`**

`apps/backend/src/main_graph/subgraphs/analysis/deepagent/state.py`:
```python
"""Root state schema for the analysis subgraph's deep agent.

Verified against deepagents==0.6.12
(deepagents/middleware/subagents.py::_return_command_with_state_update):
every key a CompiledSubAgent's runnable returns, other than
messages/todos/structured_response, merges into the ROOT deep agent's state
through ordinary LangGraph reducers via Command(update=...). bundle_ids and
agent_calls below use the same Annotated[list, operator.add] pattern
AnalysisState already uses for the same purpose.
"""

from __future__ import annotations

import operator
from typing import Annotated

from deepagents import DeepAgentState


class AnalysisDeepAgentState(DeepAgentState):
    job_id: str
    prep_result_id: str
    bundle_ids: Annotated[list[str], operator.add]
    agent_calls: Annotated[list[dict], operator.add]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_state_passthrough.py -v`
Expected: PASS. If it fails with a *different* error (e.g. deepagents' internal graph shape changed), stop and re-verify the D4 mechanism in the spec against the actually-installed version (`uv run python -c "import deepagents, inspect; print(inspect.getsource(deepagents.middleware.subagents))"`) before continuing to later tasks — they all depend on this holding.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/uv.lock \
  apps/backend/src/main_graph/subgraphs/analysis/deepagent/ \
  apps/backend/tests/unit/subgraphs/analysis/deepagent/test_state_passthrough.py
git commit -m "feat: add deepagents dependency and verify subagent state passthrough"
```

---

### Task 2: Coverage-gate pure functions

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/coverage.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `WHOLE_TREE_AGENT_TYPES: set[str]`, `PACKAGE_SCOPED_AGENT_TYPES: set[str]`,
  `compute_missing_direct_deps(agent_calls: list[dict], direct_deps: list[str]) -> list[str]`.
  Task 3 imports `WHOLE_TREE_AGENT_TYPES` for the D8 no-op check. Task 5 imports
  `compute_missing_direct_deps` for `coverage_gate`. Task 4 imports
  `PACKAGE_SCOPED_AGENT_TYPES` to pick backstop agent types.

- [ ] **Step 1: Write the failing tests**

`apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage.py`:
```python
from __future__ import annotations

from src.main_graph.subgraphs.analysis.deepagent.coverage import (
    PACKAGE_SCOPED_AGENT_TYPES,
    WHOLE_TREE_AGENT_TYPES,
    compute_missing_direct_deps,
)


def test_whole_tree_and_package_scoped_sets_partition_known_agent_types():
    from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY

    assert WHOLE_TREE_AGENT_TYPES == {"vulnerability_agent", "license_agent"}
    assert PACKAGE_SCOPED_AGENT_TYPES == set(REGISTRY) - WHOLE_TREE_AGENT_TYPES
    assert WHOLE_TREE_AGENT_TYPES.isdisjoint(PACKAGE_SCOPED_AGENT_TYPES)


def test_all_direct_deps_covered_returns_empty():
    agent_calls = [
        {"agent_type": "web_research_agent", "packages_to_focus": ["left-pad", "chalk"]},
        {"agent_type": "maintenance_agent", "packages_to_focus": ["left-pad"]},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["left-pad", "chalk"])
    assert missing == []


def test_some_direct_deps_uncovered_are_reported():
    agent_calls = [
        {"agent_type": "web_research_agent", "packages_to_focus": ["left-pad"]},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["left-pad", "chalk", "uuid"])
    assert missing == ["chalk", "uuid"]


def test_whole_tree_agent_calls_do_not_count_as_coverage():
    agent_calls = [
        {"agent_type": "vulnerability_agent", "packages_to_focus": []},
        {"agent_type": "license_agent", "packages_to_focus": []},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["left-pad", "chalk"])
    assert missing == ["left-pad", "chalk"]


def test_no_agent_calls_means_everything_missing():
    missing = compute_missing_direct_deps([], ["left-pad", "chalk"])
    assert missing == ["left-pad", "chalk"]


def test_missing_list_is_order_stable_by_direct_deps_order():
    agent_calls = [
        {"agent_type": "web_research_agent", "packages_to_focus": ["chalk"]},
    ]
    missing = compute_missing_direct_deps(agent_calls, ["uuid", "chalk", "left-pad"])
    assert missing == ["uuid", "left-pad"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `coverage.py`**

```python
"""Deterministic coverage guarantee for the analysis deep agent (spec D5, D8).

The deep agent decides HOW to investigate; whether every direct dependency
got looked at by a package-scoped agent is never left to its judgment.
"""

from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY

WHOLE_TREE_AGENT_TYPES: set[str] = {"vulnerability_agent", "license_agent"}
"""Agents that scan the entire dependency tree in one run -- a second
dispatch adds no coverage and is capped to one run/job (D8), and they never
count toward direct-dependency coverage in compute_missing_direct_deps."""

PACKAGE_SCOPED_AGENT_TYPES: set[str] = set(REGISTRY) - WHOLE_TREE_AGENT_TYPES


def compute_missing_direct_deps(
    agent_calls: list[dict], direct_deps: list[str]
) -> list[str]:
    """Direct deps with no package-scoped AgentCallRecord covering them.

    agent_calls: list of AgentCallRecord.model_dump()-shaped dicts
    (agent_type, packages_to_focus, ...). Order of the returned list follows
    direct_deps, not agent_calls.
    """
    covered: set[str] = set()
    for call in agent_calls:
        if call.get("agent_type") in PACKAGE_SCOPED_AGENT_TYPES:
            covered.update(call.get("packages_to_focus") or [])
    return [dep for dep in direct_deps if dep not in covered]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_coverage.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/coverage.py \
  apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage.py
git commit -m "feat: add deterministic direct-dependency coverage check"
```

---

### Task 3: `CompiledSubAgent` wrapper factory

Each wrapper reuses `agent_class().run()` unchanged (D2). The root deep agent hands it a free-text task description (deepagents' `task()` tool contract — the root has no way to call a Pydantic-typed tool here, confirmed in the spec's verified section); the wrapper's first step turns that into a real `AgentDispatch` via one small structured-output call, then proceeds exactly like today's `domain_agent.py`.

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py`

**Interfaces:**
- Consumes: `WHOLE_TREE_AGENT_TYPES` (Task 2), `AnalysisDeepAgentState`-shaped dict keys `job_id`/`prep_result_id`/`agent_calls` (Task 1), `REGISTRY` (`src.main_graph.subgraphs.analysis.agents.registry`), `get_services` (`src.main_graph.config`), `AgentDispatch`/`AgentCallRecord` (`src.models.results`).
- Produces: `build_agent_subagent(agent_type: str) -> deepagents.CompiledSubAgent`. Task 5 calls this once per entry in `REGISTRY` to build the `subagents=[...]` list passed to `create_deep_agent`.

- [ ] **Step 1: Write the failing test**

`apps/backend/tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py`:
```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper import (
    build_agent_subagent,
)
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"chalk": "5.0.0"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )


def _make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        domain="maintenance",
        hypothesis="chalk may be unmaintained",
        packages_to_focus=["chalk"],
        findings=[
            FindingNote(
                dep_name="chalk",
                severity="low",
                description="stale",
                evidence=[EvidenceRef(tool="npm_outdated", url=None, log_snippet="")],
            )
        ],
        summary="1 finding",
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_wrapper_extracts_dispatch_runs_agent_and_saves_bundle():
    subagent = build_agent_subagent("maintenance_agent")
    assert subagent["name"] == "maintenance_agent"

    fake_dispatch = AgentDispatch(
        domain="maintenance",
        hypothesis="chalk may be unmaintained",
        packages_to_focus=["chalk"],
        agent_type="maintenance_agent",
    )
    fake_bundle = _make_bundle()
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(return_value="bundle-123")

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(return_value=fake_dispatch),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper.get_services",
            return_value={"result_dao": fake_dao, "container": MagicMock(), "input_cache": None},
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.registry.REGISTRY"
            "['maintenance_agent'].run",
            new=AsyncMock(return_value=(fake_bundle, ["npm_outdated"], 1)),
        ),
    ):
        result = await subagent["runnable"].ainvoke(
            {
                "messages": [HumanMessage(content="check chalk for maintenance risk")],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "agent_calls": [],
            },
            {"configurable": {}},
        )

    assert result["bundle_ids"] == ["bundle-123"]
    assert len(result["agent_calls"]) == 1
    record = result["agent_calls"][0]
    assert record["agent_type"] == "maintenance_agent"
    assert record["bundle_id"] == "bundle-123"
    fake_dao.save_bundle.assert_awaited_once_with(fake_bundle)


@pytest.mark.asyncio
async def test_whole_tree_agent_is_a_noop_if_already_run_this_job():
    subagent = build_agent_subagent("license_agent")
    existing_call = {
        "agent_type": "license_agent",
        "bundle_id": "bundle-existing",
        "conductor_iteration": 0,
        "domain": "license",
        "tools_used": [],
        "react_iterations": 1,
        "started_at": "2026-07-26T00:00:00Z",
        "finished_at": "2026-07-26T00:00:01Z",
    }

    with patch(
        "src.main_graph.subgraphs.analysis.agents.registry.REGISTRY['license_agent'].run"
    ) as mock_run:
        result = await subagent["runnable"].ainvoke(
            {
                "messages": [HumanMessage(content="check licenses")],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "agent_calls": [existing_call],
            },
            {"configurable": {}},
        )
        mock_run.assert_not_called()

    assert result["bundle_ids"] == ["bundle-existing"]
    assert result["agent_calls"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `subagent_wrapper.py`**

```python
"""Builds one CompiledSubAgent per registered agent_type (spec D2).

Each subagent's runnable is a one-node graph that reuses today's
agent_class().run() unchanged. The root deep agent communicates a task as
free text (deepagents' task() tool has no way to pass a typed AgentDispatch),
so the node's first step is a small structured-output call converting that
text back into an AgentDispatch -- everything after that is identical to
domain_agent.py today.
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, cast

from deepagents import CompiledSubAgent
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.deepagent.coverage import WHOLE_TREE_AGENT_TYPES
from src.models.results import AgentCallRecord, AgentDispatch
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)


class _SubagentState(TypedDict):
    messages: list
    job_id: str
    prep_result_id: str
    agent_calls: Annotated[list[dict], operator.add]
    bundle_ids: Annotated[list[str], operator.add]


async def _extract_dispatch(description: str, agent_type: str) -> AgentDispatch:
    """Turn the root agent's free-text task() description into a typed
    AgentDispatch, so agent_class().run() sees exactly what it sees today."""
    structured = _llm.with_structured_output(AgentDispatch, method="function_calling")
    dispatch = cast(
        AgentDispatch,
        await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract a dependency-analysis dispatch from this task "
                        f"description. Set agent_type to exactly '{agent_type}'."
                    ),
                },
                {"role": "user", "content": description},
            ]
        ),
    )
    return dispatch.model_copy(update={"agent_type": agent_type})


def _existing_bundle_id(agent_calls: list[dict], agent_type: str) -> str | None:
    for call in agent_calls:
        if call.get("agent_type") == agent_type:
            return call.get("bundle_id")
    return None


def build_agent_subagent(agent_type: str) -> CompiledSubAgent:
    agent_class = REGISTRY[agent_type]
    description = agent_class.description

    async def _run(state: _SubagentState) -> dict:
        agent_calls = state.get("agent_calls") or []

        if agent_type in WHOLE_TREE_AGENT_TYPES:
            existing = _existing_bundle_id(agent_calls, agent_type)
            if existing is not None:
                # D8: whole-tree agents run at most once per job.
                return {"messages": [], "bundle_ids": [existing], "agent_calls": []}

        task_description = state["messages"][-1].content
        dispatch = await _extract_dispatch(task_description, agent_type)

        svc = get_services({"configurable": {}})
        prep = await svc["result_dao"].get_prep(state["prep_result_id"])

        started_at = datetime.now(UTC).isoformat()
        bundle, tools_used, react_iterations = await agent_class().run(
            dispatch, prep, svc["container"], cache=svc.get("input_cache")
        )
        finished_at = datetime.now(UTC).isoformat()

        bundle_id = await svc["result_dao"].save_bundle(bundle)

        record = AgentCallRecord(
            conductor_iteration=0,  # no conductor-iteration concept anymore;
            # frontend rendering of this field is explicitly out of scope
            # (see spec "Out of scope").
            agent_type=agent_type,
            domain=dispatch.domain,
            tools_used=tools_used,
            react_iterations=react_iterations,
            started_at=started_at,
            finished_at=finished_at,
            bundle_id=bundle_id,
        )
        return {
            "messages": [],
            "bundle_ids": [bundle_id],
            "agent_calls": [record.model_dump()],
        }

    graph = StateGraph(_SubagentState)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)

    return {
        "name": agent_type,
        "description": description,
        "runnable": graph.compile(),
    }
```

Note on `get_services({"configurable": {}})` in `_run`: the real `analysis_deepagent_node` (Task 5) invokes the deep agent with the outer subgraph's `RunnableConfig`, which — per Task 1's verified passthrough — reaches each subagent's own run automatically (`ensure_config` merges the ambient parent config into every nested call). This test stubs `get_services` directly rather than threading a real config through the fake LLM/graph, which is the right boundary for a unit test; the config-propagation behavior itself is what Task 1's smoke test already proves.

- [ ] **Step 4: Fix the mock patch targets to match real call sites**

The `test_whole_tree_agent_is_a_noop_if_already_run_this_job` test patches `REGISTRY['license_agent'].run` — since `agent_class` in `_run` is `REGISTRY[agent_type]` (a class, not an instance), and `_run` calls `agent_class().run(...)`, patch the class's `run` method directly as the test does. Run the tests now and adjust the mock target strings only if `AsyncMock`/`patch` report a mismatch — do not change the production code to accommodate a mock.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py \
  apps/backend/tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py
git commit -m "feat: wrap existing domain agents as deepagents CompiledSubAgents"
```

---

### Task 4: Deterministic backstop dispatch

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/backstop.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_backstop.py`

**Interfaces:**
- Consumes: `PACKAGE_SCOPED_AGENT_TYPES` (Task 2), `REGISTRY` (`src.main_graph.subgraphs.analysis.agents.registry`), `AgentDispatch`/`AgentCallRecord`/`PrepResult` (`src.models.results`).
- Produces: `async def deterministic_backstop_dispatch(missing_deps: list[str], agent_calls: list[dict], prep: PrepResult, container, dao, cache, concern: str) -> tuple[list[str], list[dict]]` returning `(new_bundle_ids, new_agent_call_dicts)`. Task 5's `backstop_dispatch_node` calls this directly.

- [ ] **Step 1: Write the failing test**

`apps/backend/tests/unit/subgraphs/analysis/deepagent/test_backstop.py`:
```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.deepagent.backstop import (
    deterministic_backstop_dispatch,
)
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import EvidenceBundle, PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"chalk": "5.0.0", "uuid": "9.0.0"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )


def _make_bundle(dep: str) -> EvidenceBundle:
    return EvidenceBundle(
        domain="backstop",
        hypothesis=f"deterministic coverage for {dep}",
        packages_to_focus=[dep],
        findings=[],
        summary="no findings",
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_backstop_covers_every_missing_dep_with_agent_types_already_used():
    agent_calls = [
        {"agent_type": "web_research_agent", "packages_to_focus": ["already-covered"]},
    ]
    prep = _make_prep()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(side_effect=["bundle-chalk", "bundle-uuid"])

    async def fake_run(dispatch, *args, **kwargs):
        return _make_bundle(dispatch.packages_to_focus[0]), [], 1

    with patch(
        "src.main_graph.subgraphs.analysis.agents.registry.REGISTRY"
        "['web_research_agent'].run",
        new=fake_run,
    ):
        bundle_ids, new_calls = await deterministic_backstop_dispatch(
            missing_deps=["chalk", "uuid"],
            agent_calls=agent_calls,
            prep=prep,
            container=MagicMock(),
            dao=fake_dao,
            cache=None,
            concern="license and maintenance risk",
        )

    assert bundle_ids == ["bundle-chalk", "bundle-uuid"]
    assert [c["agent_type"] for c in new_calls] == ["web_research_agent", "web_research_agent"]
    assert [c["bundle_id"] for c in new_calls] == ["bundle-chalk", "bundle-uuid"]


@pytest.mark.asyncio
async def test_backstop_defaults_to_web_research_agent_if_no_package_scoped_agent_ran():
    prep = _make_prep()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(return_value="bundle-1")

    async def fake_run(dispatch, *args, **kwargs):
        return _make_bundle(dispatch.packages_to_focus[0]), [], 1

    with patch(
        "src.main_graph.subgraphs.analysis.agents.registry.REGISTRY"
        "['web_research_agent'].run",
        new=fake_run,
    ):
        bundle_ids, new_calls = await deterministic_backstop_dispatch(
            missing_deps=["chalk"],
            agent_calls=[],  # no whole-tree, no package-scoped calls at all
            prep=prep,
            container=MagicMock(),
            dao=fake_dao,
            cache=None,
            concern="license and maintenance risk",
        )

    assert bundle_ids == ["bundle-1"]
    assert new_calls[0]["agent_type"] == "web_research_agent"


@pytest.mark.asyncio
async def test_backstop_failure_on_one_dep_does_not_block_the_rest():
    prep = _make_prep()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(return_value="bundle-uuid")

    call_count = {"n": 0}

    async def fake_run(dispatch, *args, **kwargs):
        call_count["n"] += 1
        if dispatch.packages_to_focus[0] == "chalk":
            raise RuntimeError("boom")
        return _make_bundle(dispatch.packages_to_focus[0]), [], 1

    with patch(
        "src.main_graph.subgraphs.analysis.agents.registry.REGISTRY"
        "['web_research_agent'].run",
        new=fake_run,
    ):
        bundle_ids, new_calls = await deterministic_backstop_dispatch(
            missing_deps=["chalk", "uuid"],
            agent_calls=[{"agent_type": "web_research_agent", "packages_to_focus": []}],
            prep=prep,
            container=MagicMock(),
            dao=fake_dao,
            cache=None,
            concern="x",
        )

    assert call_count["n"] == 2  # both attempted
    assert bundle_ids == ["bundle-uuid"]  # only the surviving one persisted
    assert len(new_calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_backstop.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `backstop.py`**

```python
"""Deterministic, no-LLM fallback for direct deps a deep agent run left
uncovered after its corrective retry budget (spec D5, D7).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.deepagent.coverage import (
    PACKAGE_SCOPED_AGENT_TYPES,
)
from src.models.results import AgentCallRecord, AgentDispatch, PrepResult

logger = logging.getLogger(__name__)

_DEFAULT_AGENT_TYPE = "web_research_agent"


def _agent_types_already_used(agent_calls: list[dict]) -> list[str]:
    used = {
        c["agent_type"]
        for c in agent_calls
        if c.get("agent_type") in PACKAGE_SCOPED_AGENT_TYPES
    }
    return sorted(used) if used else [_DEFAULT_AGENT_TYPE]


async def deterministic_backstop_dispatch(
    missing_deps: list[str],
    agent_calls: list[dict],
    prep: PrepResult,
    container,
    dao,
    cache,
    concern: str,
) -> tuple[list[str], list[dict]]:
    agent_types = _agent_types_already_used(agent_calls)
    bundle_ids: list[str] = []
    new_calls: list[dict] = []

    for dep in missing_deps:
        for agent_type in agent_types:
            agent_class = REGISTRY[agent_type]
            dispatch = AgentDispatch(
                domain="coverage_backstop",
                hypothesis=(
                    f"Deterministic backstop coverage for '{dep}' "
                    f"against concern: {concern}"
                ),
                packages_to_focus=[dep],
                agent_type=agent_type,
            )
            started_at = datetime.now(UTC).isoformat()
            try:
                bundle, tools_used, react_iterations = await agent_class().run(
                    dispatch, prep, container, cache=cache
                )
            except Exception:
                logger.warning(
                    "deterministic_backstop_dispatch: %s failed for %s",
                    agent_type,
                    dep,
                    exc_info=True,
                )
                continue
            finished_at = datetime.now(UTC).isoformat()

            bundle_id = await dao.save_bundle(bundle)
            record = AgentCallRecord(
                conductor_iteration=0,
                agent_type=agent_type,
                domain=dispatch.domain,
                tools_used=tools_used,
                react_iterations=react_iterations,
                started_at=started_at,
                finished_at=finished_at,
                bundle_id=bundle_id,
            )
            bundle_ids.append(bundle_id)
            new_calls.append(record.model_dump())

    return bundle_ids, new_calls
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_backstop.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/backstop.py \
  apps/backend/tests/unit/subgraphs/analysis/deepagent/test_backstop.py
git commit -m "feat: add deterministic backstop dispatch for uncovered direct deps"
```

---

### Task 5: `analysis_deepagent_node`, `coverage_gate`, and graph rewiring

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/state.py`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/graph.py`
- Delete: `apps/backend/src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`
- Delete: `apps/backend/src/main_graph/subgraphs/analysis/nodes/domain_agent.py`
- Delete: `apps/backend/src/main_graph/subgraphs/analysis/nodes/evidence_collector.py`
- Delete: `apps/backend/src/main_graph/subgraphs/analysis/nodes/agent_dispatcher.py`
- Delete: `apps/backend/tests/unit/test_analysis_conductor.py`
- Delete: `apps/backend/tests/unit/test_analysis_routing.py`

**Interfaces:**
- Consumes: `AnalysisDeepAgentState` (Task 1), `build_agent_subagent` (Task 3), `compute_missing_direct_deps` (Task 2), `deterministic_backstop_dispatch` (Task 4), `REGISTRY`/`get_agent_descriptions` (`registry.py`), `get_services` (`src.main_graph.config`).
- Produces: `analysis_deepagent_node`, `coverage_gate`, `backstop_dispatch_node` — three `AnalysisState -> dict` node functions Task 6's integration test drives through the compiled subgraph.

- [ ] **Step 1: Update `AnalysisState`**

Modify `apps/backend/src/main_graph/subgraphs/analysis/state.py` — replace the whole file:
```python
from __future__ import annotations

import operator
from typing import Annotated, NotRequired

from typing_extensions import TypedDict


class AnalysisState(TypedDict):
    # From MainState (matched by key name)
    job_id: str
    concern: str
    prep_result_id: str

    # Internal — deep agent run + coverage loop
    deepagent_state: NotRequired[dict]  # last full state returned by deep_agent.ainvoke()
    missing_deps: NotRequired[list[str]]
    correction_rounds: NotRequired[int]
    bundle_ids: Annotated[list[str], operator.add]
    agent_calls: Annotated[
        list[dict], operator.add
    ]  # AgentCallRecord.model_dump() per domain_agent call

    # Output (written back to MainState)
    analysis_result_id: NotRequired[str]
```

(Removed `conductor_decision`, `current_dispatch`, `conductor_iteration` — no conductor anymore. `save_analysis_result.py` reads `state.get("conductor_iteration") or 0` for `AnalysisResult.iteration_count` — check that call site now and pass `0` there or repurpose the field; see Step 4 below.)

- [ ] **Step 2: Check `save_analysis_result.py`'s dependency on `conductor_iteration`**

Run: `grep -n "conductor_iteration" apps/backend/src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`

It reads `state.get("conductor_iteration") or 0` to populate `AnalysisResult.iteration_count`. Since `AnalysisState` no longer has that key, `state.get("conductor_iteration")` will always return `None` via `.get()` (safe — `TypedDict.get` doesn't raise on a missing key at runtime) and fall back to `0`. This satisfies "don't modify `save_analysis_result.py`" from Global Constraints — leave it as-is; `iteration_count` will just always be `0` for jobs that went through the new subgraph, which is accurate (there's no conductor iteration count in this design) and does not break any typed contract (`AnalysisResult.iteration_count: int` already accepts `0`).

- [ ] **Step 3: Write `nodes.py`**

`apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py`:
```python
"""The three nodes that replace analysis_conductor / _after_conductor /
domain_agent / evidence_collector (spec D1)."""

from __future__ import annotations

import textwrap

from deepagents import create_deep_agent
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import (
    REGISTRY,
    get_agent_descriptions,
)
from src.main_graph.subgraphs.analysis.deepagent.backstop import (
    deterministic_backstop_dispatch,
)
from src.main_graph.subgraphs.analysis.deepagent.coverage import (
    compute_missing_direct_deps,
)
from src.main_graph.subgraphs.analysis.deepagent.state import AnalysisDeepAgentState
from src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper import (
    build_agent_subagent,
)
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.utils.llm import Model, get_llm

_MAX_CORRECTION_ROUNDS = 2

_SYSTEM_TEMPLATE = textwrap.dedent("""\
    You are a dependency risk investigation agent for a Node.js project.
    Your job: given a user concern and project context, delegate to the right
    specialist subagents to collect evidence, then stop once you have enough
    evidence to support a complete risk report.

    Available specialists (call via the task tool):
    {roster}

    - Delegate to a subagent as many or as few times as the concern needs.
    - You may delegate to the same specialist multiple times with different
      packages or a different angle.
    - vulnerability_agent and license_agent each scan the ENTIRE dependency
      tree in a single run -- delegate to each at most once.
    - For every other specialist, make sure your delegated tasks collectively
      cover every direct dependency relevant to the concern -- you may be
      asked to cover specific missing ones if you stop early.

    Direct dependencies (name@installed_version): {direct_deps}
    Concern: {concern}
    Project context: {context}
    """).strip()


def _roster() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in get_agent_descriptions().items())


_RECURSION_LIMIT = 50
"""Hard backstop per spec D6 -- the same role _MAX_ITERATIONS plays for the
old conductor. CompiledSubAgents can't themselves spawn further subagents
(flat one-node graphs, see subagent_wrapper.py), so this only bounds the
root deep agent's own step count, not an unbounded recursive fan-out."""


def _build_deep_agent():
    # Spec D3: deliberately no `tools=` / `middleware=[CodeInterpreterMiddleware(...)]`
    # here. The root agent's only tools are task() dispatch to the five
    # CompiledSubAgents plus deepagents' own built-in filesystem/todo tools --
    # no execute_command-class tool is reachable from this agent or any
    # subagent. Do not add one without re-opening the spec's D3 decision.
    subagents = [build_agent_subagent(agent_type) for agent_type in REGISTRY]
    return create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        subagents=subagents,
        state_schema=AnalysisDeepAgentState,
    )


_deep_agent = _build_deep_agent()


async def analysis_deepagent_node(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])

    deepagent_state = state.get("deepagent_state")
    if deepagent_state is None:
        direct_deps = list(prep.dependency_graph.get("direct", {}).keys())
        system = _SYSTEM_TEMPLATE.format(
            roster=_roster(),
            direct_deps=direct_deps,
            concern=state["concern"],
            context=prep.discovery_summary[:1000],
        )
        deepagent_state = {
            "messages": [HumanMessage(content=system)],
            "job_id": state["job_id"],
            "prep_result_id": state["prep_result_id"],
            "bundle_ids": [],
            "agent_calls": [],
        }
    else:
        missing = state.get("missing_deps") or []
        deepagent_state["messages"] = [
            *deepagent_state["messages"],
            HumanMessage(
                content=(
                    "These direct dependencies still need coverage before "
                    f"you finalize: {missing}"
                )
            ),
        ]

    # deepagent_state carries the FULL accumulated bundle_ids/agent_calls
    # forward across correction rounds -- required so subagent_wrapper's D8
    # whole-tree dedup check (which reads state["agent_calls"] at the moment
    # task() fires) still sees round 1's calls in round 2. But AnalysisState's
    # own bundle_ids/agent_calls also use an operator.add reducer, so if we
    # returned the FULL accumulated lists again this round, the outer state
    # would double-count everything already reported after round 1. Track
    # what was already there before this call and return only the delta.
    prev_bundle_count = len(deepagent_state.get("bundle_ids") or [])
    prev_call_count = len(deepagent_state.get("agent_calls") or [])

    run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
    result = await _deep_agent.ainvoke(deepagent_state, run_config)

    new_bundle_ids = (result.get("bundle_ids") or [])[prev_bundle_count:]
    new_agent_calls = (result.get("agent_calls") or [])[prev_call_count:]

    return {
        "deepagent_state": result,
        "bundle_ids": new_bundle_ids,
        "agent_calls": new_agent_calls,
    }


async def coverage_gate(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    direct_deps = list(prep.dependency_graph.get("direct", {}).keys())

    missing = compute_missing_direct_deps(state.get("agent_calls") or [], direct_deps)
    return {
        "missing_deps": missing,
        "correction_rounds": (state.get("correction_rounds") or 0) + 1,
    }


def route_after_coverage_gate(state: AnalysisState) -> str:
    missing = state.get("missing_deps") or []
    if not missing:
        return "save_analysis_result"
    if (state.get("correction_rounds") or 0) <= _MAX_CORRECTION_ROUNDS:
        return "analysis_deepagent_node"
    return "backstop_dispatch"


async def backstop_dispatch_node(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])

    bundle_ids, new_calls = await deterministic_backstop_dispatch(
        missing_deps=state.get("missing_deps") or [],
        agent_calls=state.get("agent_calls") or [],
        prep=prep,
        container=svc["container"],
        dao=dao,
        cache=svc.get("input_cache"),
        concern=state["concern"],
    )
    return {"bundle_ids": bundle_ids, "agent_calls": new_calls}
```

- [ ] **Step 4: Rewrite `graph.py`**

`apps/backend/src/main_graph/subgraphs/analysis/graph.py`:
```python
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.analysis.deepagent.nodes import (
    analysis_deepagent_node,
    backstop_dispatch_node,
    coverage_gate,
    route_after_coverage_gate,
)
from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import (
    save_analysis_result,
)
from src.main_graph.subgraphs.analysis.state import AnalysisState


def build_analysis_subgraph():
    builder = StateGraph(AnalysisState)

    builder.add_node("analysis_deepagent_node", analysis_deepagent_node)
    builder.add_node("coverage_gate", coverage_gate)
    builder.add_node("backstop_dispatch", backstop_dispatch_node)
    builder.add_node("save_analysis_result", save_analysis_result)

    builder.add_edge(START, "analysis_deepagent_node")
    builder.add_edge("analysis_deepagent_node", "coverage_gate")
    builder.add_conditional_edges("coverage_gate", route_after_coverage_gate)
    builder.add_edge("backstop_dispatch", "save_analysis_result")
    builder.add_edge("save_analysis_result", END)

    return builder.compile()


analysis_subgraph = build_analysis_subgraph()
```

- [ ] **Step 5: Delete superseded files**

```bash
cd apps/backend
git rm src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py
git rm src/main_graph/subgraphs/analysis/nodes/domain_agent.py
git rm src/main_graph/subgraphs/analysis/nodes/evidence_collector.py
git rm src/main_graph/subgraphs/analysis/nodes/agent_dispatcher.py
git rm tests/unit/test_analysis_conductor.py
git rm tests/unit/test_analysis_routing.py
```

- [ ] **Step 6: Search for any remaining imports of the deleted modules**

Run: `cd apps/backend && grep -rn "analysis_conductor\|nodes.domain_agent\|nodes\.evidence_collector\|nodes\.agent_dispatcher" src tests`
Expected: no matches outside what was just deleted. Fix any stragglers before continuing (e.g. `src/main_graph/constants.py` may reference an `ANALYSIS` artifact constant used by `save_analysis_result.py` — that one stays, it's not one of the deleted files).

- [ ] **Step 7: Run mypy and ruff on the changed files**

Run: `cd apps/backend && uv run mypy src/main_graph/subgraphs/analysis/ && uv run ruff check src/main_graph/subgraphs/analysis/`
Expected: clean. Fix any type errors now (likely candidates: `AgentDispatch`/`AgentCallRecord` field mismatches, `RunnableConfig` typing on the new node functions) before moving to Task 6 — don't let type errors accumulate across tasks.

- [ ] **Step 8: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py \
  apps/backend/src/main_graph/subgraphs/analysis/state.py \
  apps/backend/src/main_graph/subgraphs/analysis/graph.py
git commit -m "feat: rewire analysis_subgraph onto the deepagent-based nodes"
```

---

### Task 6: Rewrite the blackbox integration test

This is the test that proves the whole subgraph — deep agent run, corrective retry, deterministic backstop — behaves correctly end to end, not just its individual pieces. The real fixtures this test uses already exist in `tests/subgraphs/conftest.py` — read below, don't reinvent them.

**Files:**
- Modify: `apps/backend/tests/subgraphs/test_analysis_subgraph.py` (full rewrite)

**Interfaces:**
- Consumes: everything from Tasks 1–5, plus the existing fixtures `result_dao` and `subgraph_config` from `apps/backend/tests/subgraphs/conftest.py` (unmodified — `result_dao` is a real `ResultDAO` bound to a MongoDB testcontainer with `save_prep`/`get_analysis`; `subgraph_config` is a `RunnableConfig` dict: `{"configurable": {"result_dao": result_dao, "container": <MagicMock returning (0,"","")>, "docker_tool": MagicMock(), "job_repo": AsyncMock()}}`).

The old test mocked exactly two LLM call sites: `analysis_conductor._llm` and `base_agent._llm`. The new design has **three**: the deep agent's own root model, `subagent_wrapper._llm` (extracts `AgentDispatch` from the root's free-text task description), and `base_agent._llm` (unchanged, still drives each `_react_loop`). Be upfront in the test docstring that this is a real, structural cost of the swap, not an oversight.

- [ ] **Step 1: Write the new test file**

Replace `apps/backend/tests/subgraphs/test_analysis_subgraph.py` in full:

```python
"""
Blackbox integration test for the analysis subgraph (deepagent-based).

What is real:
- LangGraph wiring: analysis_deepagent_node -> coverage_gate ->
  (loop | backstop_dispatch | save_analysis_result)
- save_analysis_result (MongoDB persistence via testcontainer)
- AnalysisState accumulation (bundle_ids, agent_calls) via reducers
- Every domain-agent's actual run() logic (only each agent's underlying LLM
  call is mocked, same as before)

What is mocked (three LLM call sites now, up from two before the swap):
- The deep agent's own root model (scripted task() tool calls)
- subagent_wrapper._llm (extracts AgentDispatch from the root's free-text
  task description)
- base_agent._llm (returns a canned DomainAgentDecision, same as before)
- vulnerability_agent.npm_audit (deterministic agent, not an LLM call)
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from src.main_graph.subgraphs.analysis.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.analysis.graph import build_analysis_subgraph
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch, DomainAgentDecision, PrepResult


class _ScriptedToolCallingChatModel(FakeMessagesListChatModel):
    """See tests/unit/subgraphs/analysis/deepagent/test_state_passthrough.py
    for why bind_tools must be overridden as a no-op here."""

    def bind_tools(
        self, tools: Sequence[Any], **kwargs: Any
    ) -> "_ScriptedToolCallingChatModel":
        return self


def _seed_prep(job_id: str) -> PrepResult:
    return PrepResult(
        job_id=job_id,
        repo_path="/tmp/test-repo",
        project_metadata={
            "name": "test-project",
            "package_manager": "npm",
            "direct_dependencies_count": 1,
            "transitive_dependencies_count": 0,
        },
        manifest_files=["package.json", "package-lock.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
        discovery_summary="test-project depends on lodash.",
        vector_store_id="",
    )


# vulnerability_agent is deterministic: it runs `npm audit` (not the LLM) and
# extracts every advisory. Feed it a canned audit result so the graph wiring
# can be exercised without a real repo -- same fixture the old test used.
_AUDIT_FIXTURE = {
    "advisories": {
        "1": {
            "module_name": "lodash",
            "severity": "high",
            "title": "CVE-2021-23337: prototype pollution in lodash < 4.17.21",
            "vulnerable_versions": "<4.17.21",
            "patched_versions": ">=4.17.21",
            "cves": ["CVE-2021-23337"],
            "url": None,
            "findings": [{"version": "4.17.20"}],
        }
    }
}


def _task_call(description: str, subagent_type: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": description, "subagent_type": subagent_type},
                "id": call_id,
            }
        ],
    )


def _build_fake_deep_agent(root_responses: list[AIMessage]):
    """Builds a real deep agent with a scripted root model, bypassing
    get_llm entirely so no patch-ordering trap is possible (patching
    nodes.get_llm and then calling nodes._build_deep_agent() inside the same
    `with` block would evaluate _build_deep_agent() before the patch takes
    effect, since context-manager arguments are evaluated eagerly)."""
    fake_model = _ScriptedToolCallingChatModel(responses=root_responses)
    subagents = [
        deepagent_nodes.build_agent_subagent(agent_type)
        for agent_type in deepagent_nodes.REGISTRY
    ]
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=fake_model,
        subagents=subagents,
        state_schema=deepagent_nodes.AnalysisDeepAgentState,
    )


@pytest.mark.asyncio
async def test_analysis_dispatches_agent_and_saves_result(subgraph_config, result_dao):
    """Root deep agent delegates once to vulnerability_agent, finalizes --
    AnalysisResult with the lodash CVE finding lands in MongoDB."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [
            _task_call(
                "Check lodash@4.17.20 for known CVEs.",
                "vulnerability_agent",
                "call_1",
            ),
            AIMessage(content="Sufficient evidence collected, finalizing."),
        ]
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(
                return_value=AgentDispatch(
                    domain="vulnerability",
                    hypothesis="Check for known CVEs in lodash 4.17.20",
                    packages_to_focus=["lodash"],
                    agent_type="vulnerability_agent",
                )
            ),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.npm_audit",
            AsyncMock(return_value=_AUDIT_FIXTURE),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "security vulnerabilities",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert analysis.job_id == job_id
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"
    assert analysis.findings[0].severity == "high"
    assert len(analysis.evidence_bundle_ids) == 1


@pytest.mark.asyncio
async def test_backstop_fires_when_deep_agent_never_delegates(
    subgraph_config, result_dao
):
    """Root deep agent finalizes immediately without calling task() at all --
    coverage_gate must route through backstop_dispatch after
    _MAX_CORRECTION_ROUNDS, and lodash still ends up covered without any
    further LLM involvement in the backstop path itself (vulnerability_agent
    is deterministic, so this exercises the backstop with zero extra LLM
    mocking beyond the deep agent's own root model)."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [AIMessage(content="Nothing to check here, finalizing.")]
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.npm_audit",
            AsyncMock(return_value=_AUDIT_FIXTURE),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "security vulnerabilities",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    # The deep agent never delegated to vulnerability_agent itself, but the
    # deterministic backstop must have -- lodash's CVE still surfaces.
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"

    job_repo = subgraph_config["configurable"]["job_repo"]
    job_repo.update_artifact_data.assert_awaited_once()
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert any(c["agent_type"] == "vulnerability_agent" for c in agent_calls)


@pytest.mark.asyncio
async def test_analysis_accumulates_bundles_from_two_delegations(
    subgraph_config, result_dao
):
    """Root deep agent delegates to two different subagents (parallel-style
    task() calls in one turn, or sequential -- either way both must
    accumulate via the Annotated[list, operator.add] reducers on
    AnalysisDeepAgentState / AnalysisState)."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "Check lodash for CVEs.",
                            "subagent_type": "vulnerability_agent",
                        },
                        "id": "call_1",
                    },
                    {
                        "name": "task",
                        "args": {
                            "description": "Check whether lodash is maintained.",
                            "subagent_type": "maintenance_agent",
                        },
                        "id": "call_2",
                    },
                ],
            ),
            AIMessage(content="Both checks done, finalizing."),
        ]
    )

    maintenance_decision = DomainAgentDecision(
        tool_calls=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="medium",
                description="Finding from maintenance agent",
                evidence=[
                    EvidenceRef(tool="npm_outdated", url=None, log_snippet="")
                ],
            )
        ],
        summary="One finding found",
        confidence=0.8,
        finalize=True,
        reasoning="done",
    )

    async def _extract(description: str, agent_type: str) -> AgentDispatch:
        return AgentDispatch(
            domain=agent_type,
            hypothesis=description,
            packages_to_focus=["lodash"],
            agent_type=agent_type,
        )

    base_llm_chain = MagicMock()
    base_llm_chain.ainvoke = AsyncMock(return_value=maintenance_decision)
    fake_base_llm = MagicMock()
    fake_base_llm.with_structured_output = MagicMock(return_value=base_llm_chain)

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(side_effect=_extract),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.npm_audit",
            AsyncMock(return_value=_AUDIT_FIXTURE),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            fake_base_llm,
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "dependency health",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert len(analysis.evidence_bundle_ids) == 2
    assert len(analysis.findings) == 2

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 2
    assert {c["agent_type"] for c in agent_calls} == {
        "vulnerability_agent",
        "maintenance_agent",
    }
```

- [ ] **Step 2: Run the tests**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_analysis_subgraph.py -v`
Expected: all 3 tests PASS. If `deepagent_nodes.build_agent_subagent`/`deepagent_nodes.REGISTRY`/`deepagent_nodes.AnalysisDeepAgentState` aren't accessible as attributes of the `nodes` module (they're imported into it in Task 5's `nodes.py`, so they should be), fix the import re-export in `nodes.py` rather than reaching into `subagent_wrapper`/`coverage` directly from the test — keeping the test's imports pointed at `nodes` mirrors how `analysis_deepagent_node` itself consumes them.

If the two-tool-calls-in-one-turn script in the third test doesn't get scheduled the way `FakeMessagesListChatModel` cycles through `responses` (it returns one `AIMessage` per `_generate` call, and a single `AIMessage` can carry multiple `tool_calls` — confirm both `task()` calls in that one message actually execute), adjust to two separate scripted turns (`task()` call 1 -> `task()` call 2 -> finalize) if the RED step shows the harness doesn't schedule parallel tool calls from one message the way this assumes. Let the test tell you which shape is real.

- [ ] **Step 3: Run the full backend test suite**

Run: `cd apps/backend && uv run pytest`
Expected: all tests pass, including every test under `tests/unit/subgraphs/analysis/deepagent/` from Tasks 1–4 and everything untouched elsewhere (`test_save_analysis_result.py`, all `agents/` tests, remediation tests, etc.).

- [ ] **Step 4: Run mypy and ruff on the whole backend**

Run: `cd apps/backend && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/tests/subgraphs/test_analysis_subgraph.py
git commit -m "test: rewrite analysis subgraph blackbox test for the deepagent swap"
```

---

## Verification

After Task 6, do a live end-to-end run against a real (non-fixture) small repo to confirm the swap works outside of mocks, matching this codebase's existing practice of validating on real production data before considering a change done (see `docs/superpowers/specs/2026-07-20-direct-anchored-findings.md`'s e2e validation section):

```bash
cd apps/backend
uv run uvicorn src.main:app --reload-dir src &
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/chalk/chalk", "concern": "outdated or unmaintained dependencies"}'
# poll GET /analyze/{trace_id} until status is "done"
```

Confirm: `analysis` artifact completes with `status: done`, the report contains findings anchored on direct dependencies (spot-check against the direct-anchored-findings behavior this swap must not regress), and no `execute_command`-shaped tool call appears in any logged tool trace.

---

## Execution choice

Plan complete and saved to `docs/superpowers/plans/2026-07-26-analysis-subgraph-deepagent-swap.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
