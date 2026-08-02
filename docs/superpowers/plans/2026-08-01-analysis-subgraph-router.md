# Analysis Subgraph Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the analysis subgraph so simple concerns (vulnerability/license, whole-tree, no per-dependency breakdown) run directly against the relevant whole-tree agent(s) while everything else still goes through the deep agent, now with a structured `Concern`, a rewritten prompt, an enforced call budget, and coverage enforcement conditioned on whether the concern actually asked for exhaustive per-dependency treatment.

**Architecture:** A new `understand_concern` node (one LLM structured-output call) becomes the first node in the analysis subgraph, writing a typed `Concern` into `AnalysisState`. A pure-Python `route_concern` conditional edge reads it and sends the run to either a new `run_direct_agents` node (bypasses the deep agent entirely) or the existing `analysis_deepagent_node` -> `coverage_gate` chain (unchanged wiring, changed behavior: rewritten prompt, `DeepAgentLimits` budget/concurrency enforcement, and `coverage_gate` now short-circuits when `requires_per_dependency_analysis` is false).

**Tech Stack:** Python 3.12, LangGraph, Pydantic v2, `langchain_core` structured output (`with_structured_output(..., method="function_calling")`), pytest + pytest-asyncio (`asyncio_mode = "auto"`), MongoDB via `testcontainers` for the graph-level integration tests in `tests/subgraphs/`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-01-analysis-subgraph-router-design.md` (committed `1e3090e`, updated `3a4956c`). Every task below implements one numbered section of that spec.
- `MainState.concern` / the `/analyze` API contract do not change — stays a free-text string. `Concern` is internal to the analysis subgraph only.
- No placeholder code, no `TODO`s. Every new/changed function ships with real tests in the same task.
- Follow existing conventions exactly: `from __future__ import annotations` at the top of every file, module-level `_llm = get_llm(Model.GPT_5_4_MINI)` for any new LLM call site (patchable in tests the same way `coverage.py`/`subagent_wrapper.py`/`base_agent.py` already do it), `textwrap.dedent(...).strip()` for multi-line prompts.
- Run `uv run pytest <path> -v` (backend lives at `apps/backend/`; run all commands from that directory) after every task's implementation step, and `uv run ruff check src/main_graph/subgraphs/analysis/ tests/unit/subgraphs/analysis/ tests/subgraphs/` before each commit — match this repo's existing lint/format tooling.
- One implementation detail the spec's pseudocode gets wrong, fixed here: `coverage_gate`'s conditional short-circuit (spec section 8) must NOT reconstruct a full `Concern(**state["structured_concern"])`, because the spec's own documented fallback ("if `requires_per_dependency_analysis` is missing, default to `True`") would crash `Concern`'s validation on a dict missing other required fields. Task 9 below reads the raw dict field directly instead: `(state.get("structured_concern") or {}).get("requires_per_dependency_analysis", True)`. `route_concern` and `run_direct_agents` (Tasks 1 and 5) keep the full `Concern(**...)` reconstruction — safe there because `understand_concern` always populates a complete `Concern` before either of those runs.

---

## File Structure

**New files:**
- `apps/backend/src/main_graph/subgraphs/analysis/concern.py` — `Concern`, `ConcernType`, `ConcernScope`, `SIMPLE_CONCERN_TYPES`, `is_simple`, `route_concern`.
- `apps/backend/src/main_graph/subgraphs/analysis/nodes/understand_concern.py` — the `understand_concern` node.
- `apps/backend/src/main_graph/subgraphs/analysis/deepagent/limits.py` — `DeepAgentLimits`, `DEEPAGENT_LIMITS`, `SPECIALIST_SEMAPHORE`.
- `apps/backend/src/main_graph/subgraphs/analysis/deepagent/specialist_runner.py` — `run_specialist`, extracted from `subagent_wrapper._run`.
- `apps/backend/src/main_graph/subgraphs/analysis/nodes/run_direct_agents.py` — the `run_direct_agents` node.
- Tests: `tests/unit/subgraphs/analysis/test_concern.py`, `tests/unit/subgraphs/analysis/nodes/test_understand_concern.py`, `tests/unit/subgraphs/analysis/deepagent/test_limits.py`, `tests/unit/subgraphs/analysis/nodes/test_run_direct_agents.py`, `tests/unit/subgraphs/analysis/deepagent/test_prompt.py`, `tests/unit/subgraphs/analysis/deepagent/test_coverage_gate.py`, `tests/subgraphs/test_analysis_subgraph_router.py`.

**Modified files:**
- `apps/backend/src/main_graph/subgraphs/analysis/state.py` — add `structured_concern` field.
- `apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py` — use `run_specialist`; enforce `DEEPAGENT_LIMITS`.
- `apps/backend/src/main_graph/subgraphs/analysis/graph.py` — wire `understand_concern` / `route_concern` / `run_direct_agents`.
- `apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py` — rewritten `_SYSTEM_TEMPLATE`, concern interpolation, conditional `coverage_gate`.
- `apps/backend/tests/subgraphs/test_analysis_subgraph.py` — mock `understand_concern`'s LLM call in all 5 existing tests so they keep exercising the deep-agent path.

---

### Task 1: `Concern` schema, `is_simple`, `route_concern`

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/concern.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/test_concern.py`

**Interfaces:**
- Produces: `Concern` (pydantic `BaseModel`: `type: list[ConcernType]`, `scope: ConcernScope`, `packages: list[str]`, `requires_per_dependency_analysis: bool`, `preferred_agents: list[str]`), `SIMPLE_CONCERN_TYPES: set[str]`, `is_simple(concern: Concern) -> bool`, `route_concern(state: AnalysisState) -> str` (returns `"simple"` or `"complex"`, reads `state["structured_concern"]`).

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/tests/unit/subgraphs/analysis/test_concern.py
from __future__ import annotations

from src.main_graph.subgraphs.analysis.concern import Concern, is_simple, route_concern


def _concern(**overrides) -> Concern:
    defaults = dict(
        type=["vulnerability"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["vulnerability_agent"],
    )
    defaults.update(overrides)
    return Concern(**defaults)


def test_vulnerability_only_is_simple():
    assert is_simple(_concern(type=["vulnerability"])) is True


def test_license_only_is_simple():
    assert (
        is_simple(_concern(type=["license"], preferred_agents=["license_agent"]))
        is True
    )


def test_vulnerability_and_license_is_simple():
    concern = _concern(
        type=["vulnerability", "license"],
        preferred_agents=["vulnerability_agent", "license_agent"],
    )
    assert is_simple(concern) is True


def test_maintenance_type_forces_complex():
    concern = _concern(type=["maintenance"], preferred_agents=["maintenance_agent"])
    assert is_simple(concern) is False


def test_mixed_simple_and_complex_type_forces_complex():
    assert is_simple(_concern(type=["vulnerability", "maintenance"])) is False


def test_requires_per_dependency_analysis_forces_complex():
    assert is_simple(_concern(requires_per_dependency_analysis=True)) is False


def test_specific_packages_scope_forces_complex():
    concern = _concern(scope="specific_packages", packages=["lodash"])
    assert is_simple(concern) is False


def test_route_concern_returns_simple():
    state = {"structured_concern": _concern().model_dump()}
    assert route_concern(state) == "simple"


def test_route_concern_returns_complex():
    concern = _concern(type=["maintenance"], preferred_agents=["maintenance_agent"])
    state = {"structured_concern": concern.model_dump()}
    assert route_concern(state) == "complex"
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `apps/backend/`): `uv run pytest tests/unit/subgraphs/analysis/test_concern.py -v`
Expected: FAIL/ERROR — `src.main_graph.subgraphs.analysis.concern` does not exist yet.

- [ ] **Step 3: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/concern.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.main_graph.subgraphs.analysis.state import AnalysisState

ConcernType = Literal[
    "vulnerability", "license", "maintenance", "supply_chain", "web_research", "other"
]
ConcernScope = Literal["all_dependencies", "specific_packages"]


class Concern(BaseModel):
    type: list[ConcernType]
    scope: ConcernScope
    packages: list[str] = Field(default_factory=list)
    requires_per_dependency_analysis: bool
    preferred_agents: list[str]


SIMPLE_CONCERN_TYPES = {"vulnerability", "license"}


def is_simple(concern: Concern) -> bool:
    return (
        set(concern.type) <= SIMPLE_CONCERN_TYPES
        and not concern.requires_per_dependency_analysis
        and concern.scope == "all_dependencies"
    )


def route_concern(state: AnalysisState) -> str:
    concern = Concern(**state["structured_concern"])
    return "simple" if is_simple(concern) else "complex"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/analysis/test_concern.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/concern.py apps/backend/tests/unit/subgraphs/analysis/test_concern.py
git commit -m "feat: add structured Concern schema and router predicate"
```

---

### Task 2: `AnalysisState.structured_concern` + `understand_concern` node

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/state.py`
- Create: `apps/backend/src/main_graph/subgraphs/analysis/nodes/understand_concern.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/nodes/test_understand_concern.py`

**Interfaces:**
- Consumes: `Concern` (Task 1), `get_services` (`src.main_graph.config`), `get_agent_descriptions` (`src.main_graph.subgraphs.analysis.agents.registry`), `Model`/`get_llm` (`src.utils.llm`).
- Produces: `understand_concern(state: AnalysisState, config: RunnableConfig) -> dict` returning `{"structured_concern": <Concern.model_dump()>}`. Module-level `_llm`, patchable at `src.main_graph.subgraphs.analysis.nodes.understand_concern._llm`.

- [ ] **Step 1: Add the state field**

In `apps/backend/src/main_graph/subgraphs/analysis/state.py`, add inside `AnalysisState`, in the "Internal" section right after `deepagent_state`:

```python
    structured_concern: NotRequired[dict]  # Concern.model_dump()
```

- [ ] **Step 2: Write the failing tests**

```python
# apps/backend/tests/unit/subgraphs/analysis/nodes/test_understand_concern.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.nodes.understand_concern import (
    understand_concern,
)
from src.models.results import PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )


@pytest.mark.asyncio
async def test_understand_concern_writes_structured_concern_to_state():
    fake_concern = Concern(
        type=["vulnerability"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["vulnerability_agent"],
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=fake_concern
    )
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            mock_llm,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern.get_services",
            mock_get_services,
        ),
    ):
        result = await understand_concern(
            {
                "job_id": "job-1",
                "concern": "check for known CVEs",
                "prep_result_id": "prep-1",
            },
            {"configurable": {}},
        )

    assert result["structured_concern"] == fake_concern.model_dump()
    mock_llm.with_structured_output.assert_called_once_with(
        Concern, method="function_calling"
    )


@pytest.mark.asyncio
async def test_understand_concern_passes_direct_deps_and_roster_as_context():
    fake_concern = Concern(
        type=["license"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["license_agent"],
    )
    captured: dict = {}

    async def _ainvoke(messages):
        captured["messages"] = messages
        return fake_concern

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=_ainvoke
    )
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            mock_llm,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern.get_services",
            mock_get_services,
        ),
    ):
        await understand_concern(
            {
                "job_id": "job-1",
                "concern": "check licenses",
                "prep_result_id": "prep-1",
            },
            {"configurable": {}},
        )

    system_content = captured["messages"][0]["content"]
    assert "lodash@4.17.20" in system_content
    assert "vulnerability_agent" in system_content
    assert captured["messages"][1]["content"] == "check licenses"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/analysis/nodes/test_understand_concern.py -v`
Expected: FAIL/ERROR — module does not exist yet.

- [ ] **Step 4: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/nodes/understand_concern.py
from __future__ import annotations

import textwrap
from typing import cast

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import get_agent_descriptions
from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)

_UNDERSTAND_CONCERN_SYSTEM = textwrap.dedent("""\
    Classify a user's dependency-risk concern for a Node.js project into a
    structured form.

    Available specialist agents (valid values for preferred_agents):
    {agent_roster}

    Direct dependencies (name@installed_version): {direct_deps}

    Rules:
    - type: one or more of "vulnerability", "license", "maintenance",
      "supply_chain", "web_research", "other" -- every concept explicitly
      present in the concern. Do not add types the concern doesn't mention.
    - scope: "specific_packages" if the concern names particular package(s);
      otherwise "all_dependencies".
    - packages: the specific package names if scope is "specific_packages",
      else empty.
    - requires_per_dependency_analysis: true only if the concern explicitly
      asks for a per-package/per-dependency breakdown or similarly
      exhaustive individual treatment of every dependency. False for a
      general/aggregate risk read.
    - preferred_agents: the specialist agent_type(s) from the roster above
      best suited to investigate this concern -- vulnerability_agent for
      "vulnerability", license_agent for "license", maintenance_agent for
      "maintenance", supply_chain_agent for "supply_chain",
      web_research_agent for "web_research" or "other".
    """).strip()


def _roster() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in get_agent_descriptions().items())


async def understand_concern(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    direct_deps = [
        f"{n}@{v}" for n, v in prep.dependency_graph.get("direct", {}).items()
    ]

    structured = _llm.with_structured_output(Concern, method="function_calling")
    concern = cast(
        Concern,
        await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": _UNDERSTAND_CONCERN_SYSTEM.format(
                        agent_roster=_roster(), direct_deps=direct_deps
                    ),
                },
                {"role": "user", "content": state["concern"]},
            ]
        ),
    )
    return {"structured_concern": concern.model_dump()}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/analysis/nodes/test_understand_concern.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/state.py apps/backend/src/main_graph/subgraphs/analysis/nodes/understand_concern.py apps/backend/tests/unit/subgraphs/analysis/nodes/test_understand_concern.py
git commit -m "feat: add understand_concern node"
```

---

### Task 3: `DeepAgentLimits` and the shared specialist semaphore

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/limits.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_limits.py`

**Interfaces:**
- Produces: `DeepAgentLimits` (frozen dataclass: `max_specialist_calls: int = 8`, `max_parallel_calls: int = 3`), `DEEPAGENT_LIMITS: DeepAgentLimits`, `SPECIALIST_SEMAPHORE: asyncio.Semaphore` (sized to `DEEPAGENT_LIMITS.max_parallel_calls`). Used by Tasks 5 and 6 for concurrency, and Tasks 6 and 8 for budget/prompt numbers.

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/tests/unit/subgraphs/analysis/deepagent/test_limits.py
from __future__ import annotations

import asyncio
import dataclasses

import pytest

from src.main_graph.subgraphs.analysis.deepagent.limits import (
    DEEPAGENT_LIMITS,
    SPECIALIST_SEMAPHORE,
    DeepAgentLimits,
)


def test_default_limits():
    assert DEEPAGENT_LIMITS.max_specialist_calls == 8
    assert DEEPAGENT_LIMITS.max_parallel_calls == 3


def test_limits_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEEPAGENT_LIMITS.max_specialist_calls = 100


def test_specialist_semaphore_is_sized_to_max_parallel_calls():
    assert isinstance(SPECIALIST_SEMAPHORE, asyncio.Semaphore)
    assert SPECIALIST_SEMAPHORE._value == DeepAgentLimits().max_parallel_calls
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_limits.py -v`
Expected: FAIL/ERROR — module does not exist yet.

- [ ] **Step 3: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/deepagent/limits.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class DeepAgentLimits:
    max_specialist_calls: int = 8
    max_parallel_calls: int = 3


DEEPAGENT_LIMITS = DeepAgentLimits()
SPECIALIST_SEMAPHORE = asyncio.Semaphore(DEEPAGENT_LIMITS.max_parallel_calls)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_limits.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/limits.py apps/backend/tests/unit/subgraphs/analysis/deepagent/test_limits.py
git commit -m "feat: add DeepAgentLimits and the shared specialist semaphore"
```

---

### Task 4: Extract `run_specialist` from `subagent_wrapper._run`

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/specialist_runner.py`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py`
- Test: existing `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py` (no changes needed — this task is a pure refactor, verified by keeping it green)

**Interfaces:**
- Produces: `run_specialist(agent_type: str, dispatch: AgentDispatch, prep: PrepResult, svc: PipelineConfigurable) -> tuple[str, dict]` — returns `(bundle_id, AgentCallRecord.model_dump())`. Consumed by Task 5 (`run_direct_agents`) and by `subagent_wrapper._run` itself after this task.

- [ ] **Step 1: Write the failing test for the new module**

```python
# apps/backend/tests/unit/subgraphs/analysis/deepagent/test_specialist_runner.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.deepagent.specialist_runner import (
    run_specialist,
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
async def test_run_specialist_runs_agent_saves_bundle_and_builds_record():
    dispatch = AgentDispatch(
        domain="maintenance",
        hypothesis="chalk may be unmaintained",
        packages_to_focus=["chalk"],
        agent_type="maintenance_agent",
    )
    fake_bundle = _make_bundle()
    fake_dao = MagicMock()
    fake_dao.save_bundle = AsyncMock(return_value="bundle-123")
    svc = {"result_dao": fake_dao, "container": MagicMock(), "input_cache": None}

    with patch(
        "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
        ".MaintenanceAgent.run",
        new=AsyncMock(return_value=(fake_bundle, ["npm_outdated"], 1)),
    ):
        bundle_id, record = await run_specialist(
            "maintenance_agent", dispatch, _make_prep(), svc
        )

    assert bundle_id == "bundle-123"
    assert record["agent_type"] == "maintenance_agent"
    assert record["bundle_id"] == "bundle-123"
    assert record["packages_to_focus"] == ["chalk"]
    assert record["tools_used"] == ["npm_outdated"]
    fake_dao.save_bundle.assert_awaited_once_with(fake_bundle)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_specialist_runner.py -v`
Expected: FAIL/ERROR — module does not exist yet.

- [ ] **Step 3: Write `specialist_runner.py`**

```python
# apps/backend/src/main_graph/subgraphs/analysis/deepagent/specialist_runner.py
from __future__ import annotations

from datetime import UTC, datetime

from src.main_graph.config import PipelineConfigurable
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.models.results import AgentCallRecord, AgentDispatch, PrepResult


async def run_specialist(
    agent_type: str,
    dispatch: AgentDispatch,
    prep: PrepResult,
    svc: PipelineConfigurable,
) -> tuple[str, dict]:
    """Runs one specialist agent, saves its bundle, and returns
    (bundle_id, AgentCallRecord.model_dump())."""
    agent_class = REGISTRY[agent_type]
    started_at = datetime.now(UTC).isoformat()
    bundle, tools_used, react_iterations = await agent_class().run(
        dispatch, prep, svc["container"], cache=svc.get("input_cache")
    )
    finished_at = datetime.now(UTC).isoformat()
    bundle_id = await svc["result_dao"].save_bundle(bundle)
    record = AgentCallRecord(
        conductor_iteration=0,
        agent_type=agent_type,
        domain=dispatch.domain,
        packages_to_focus=dispatch.packages_to_focus,
        tools_used=tools_used,
        react_iterations=react_iterations,
        started_at=started_at,
        finished_at=finished_at,
        bundle_id=bundle_id,
    )
    return bundle_id, record.model_dump()
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_specialist_runner.py -v`
Expected: 1 passed

- [ ] **Step 5: Refactor `subagent_wrapper.py` to use `run_specialist`**

In `apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py`, replace the imports:

```python
from __future__ import annotations

import operator
from typing import Annotated, cast

from deepagents import CompiledSubAgent
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.deepagent.coverage import WHOLE_TREE_AGENT_TYPES
from src.main_graph.subgraphs.analysis.deepagent.specialist_runner import run_specialist
from src.models.results import AgentDispatch
from src.utils.llm import Model, get_llm
```

(drops `from datetime import UTC, datetime` and `AgentCallRecord` — no longer used directly in this file.)

Replace the body of `_run` from `svc = get_services(config)` through the final `return` with:

```python
        svc = get_services(config)
        prep = await svc["result_dao"].get_prep(state["prep_result_id"])

        bundle_id, record = await run_specialist(agent_type, dispatch, prep, svc)
        return {
            "messages": [],
            "bundle_ids": [bundle_id],
            "agent_calls": [record],
        }
```

- [ ] **Step 6: Run the existing subagent_wrapper tests to confirm the refactor didn't change behavior**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py -v`
Expected: 2 passed, unchanged (this file is not modified by this task — its assertions target the same external shape `run_specialist` now produces).

- [ ] **Step 7: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/specialist_runner.py apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py apps/backend/tests/unit/subgraphs/analysis/deepagent/test_specialist_runner.py
git commit -m "refactor: extract run_specialist helper out of subagent_wrapper"
```

---

### Task 5: `run_direct_agents` node (simple-path execution)

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/nodes/run_direct_agents.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/nodes/test_run_direct_agents.py`

**Interfaces:**
- Consumes: `Concern` (Task 1), `run_specialist` + `SPECIALIST_SEMAPHORE` (Tasks 4, 3).
- Produces: `run_direct_agents(state: AnalysisState, config: RunnableConfig) -> dict` returning `{"bundle_ids": [...], "agent_calls": [...]}` — same shape `analysis_deepagent_node` returns.

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/tests/unit/subgraphs/analysis/nodes/test_run_direct_agents.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.nodes.run_direct_agents import (
    run_direct_agents,
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
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )


def _bundle(domain: str) -> EvidenceBundle:
    return EvidenceBundle(
        domain=domain,
        hypothesis="check for known CVEs",
        packages_to_focus=[],
        findings=[
            FindingNote(
                dep_name="lodash",
                severity="high",
                description=f"{domain} finding",
                evidence=[EvidenceRef(tool="trivy", url=None, log_snippet="")],
            )
        ],
        summary="1 finding",
        confidence=1.0,
    )


def _state(preferred_agents: list[str]) -> dict:
    return {
        "job_id": "job-1",
        "concern": "check for known CVEs",
        "prep_result_id": "prep-1",
        "structured_concern": {
            "type": ["vulnerability"],
            "scope": "all_dependencies",
            "packages": [],
            "requires_per_dependency_analysis": False,
            "preferred_agents": preferred_agents,
        },
    }


@pytest.mark.asyncio
async def test_run_direct_agents_single_agent():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(return_value="bundle-1")
    mock_get_services = MagicMock(
        return_value={"result_dao": fake_dao, "container": MagicMock(), "input_cache": None}
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.run_direct_agents.get_services",
            mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".VulnerabilityAgent.run",
            new=AsyncMock(return_value=(_bundle("vulnerability"), ["trivy"], 1)),
        ),
    ):
        result = await run_direct_agents(
            _state(["vulnerability_agent"]), {"configurable": {}}
        )

    assert result["bundle_ids"] == ["bundle-1"]
    assert len(result["agent_calls"]) == 1
    assert result["agent_calls"][0]["agent_type"] == "vulnerability_agent"
    assert result["agent_calls"][0]["packages_to_focus"] == []


@pytest.mark.asyncio
async def test_run_direct_agents_both_agents_run_concurrently():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(side_effect=["bundle-vuln", "bundle-lic"])
    mock_get_services = MagicMock(
        return_value={"result_dao": fake_dao, "container": MagicMock(), "input_cache": None}
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.run_direct_agents.get_services",
            mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".VulnerabilityAgent.run",
            new=AsyncMock(return_value=(_bundle("vulnerability"), ["trivy"], 1)),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.license_agent.LicenseAgent.run",
            new=AsyncMock(
                return_value=(_bundle("license"), ["license_collector"], 1)
            ),
        ),
    ):
        result = await run_direct_agents(
            _state(["vulnerability_agent", "license_agent"]), {"configurable": {}}
        )

    assert set(result["bundle_ids"]) == {"bundle-vuln", "bundle-lic"}
    assert len(result["agent_calls"]) == 2
    assert {c["agent_type"] for c in result["agent_calls"]} == {
        "vulnerability_agent",
        "license_agent",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/analysis/nodes/test_run_direct_agents.py -v`
Expected: FAIL/ERROR — module does not exist yet.

- [ ] **Step 3: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/nodes/run_direct_agents.py
from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import PipelineConfigurable, get_services
from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.deepagent.limits import SPECIALIST_SEMAPHORE
from src.main_graph.subgraphs.analysis.deepagent.specialist_runner import run_specialist
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AgentDispatch, PrepResult


async def _run_one(
    agent_type: str, concern: Concern, hypothesis: str, prep: PrepResult, svc: PipelineConfigurable
) -> tuple[str, dict]:
    dispatch = AgentDispatch(
        domain=", ".join(concern.type),
        hypothesis=hypothesis,
        packages_to_focus=[],  # ignored by whole-tree agents anyway
        agent_type=agent_type,
    )
    async with SPECIALIST_SEMAPHORE:
        return await run_specialist(agent_type, dispatch, prep, svc)


async def run_direct_agents(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    prep = await svc["result_dao"].get_prep(state["prep_result_id"])
    concern = Concern(**state["structured_concern"])

    results = await asyncio.gather(
        *[
            _run_one(agent_type, concern, state["concern"], prep, svc)
            for agent_type in concern.preferred_agents
        ]
    )

    return {
        "bundle_ids": [bundle_id for bundle_id, _ in results],
        "agent_calls": [record for _, record in results],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/analysis/nodes/test_run_direct_agents.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/nodes/run_direct_agents.py apps/backend/tests/unit/subgraphs/analysis/nodes/test_run_direct_agents.py
git commit -m "feat: add run_direct_agents node for simple concerns"
```

---

### Task 6: Wire the router into `graph.py`; fix the 5 existing graph-level tests

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/graph.py`
- Modify: `apps/backend/tests/subgraphs/test_analysis_subgraph.py`

**Interfaces:**
- Consumes: `understand_concern` (Task 2), `route_concern` (Task 1), `run_direct_agents` (Task 5), all existing analysis-subgraph nodes.
- Produces: `build_analysis_subgraph()` now starts at `understand_concern` and branches on `route_concern`.

This task has no new unit tests of its own — the deliverable is "the graph still builds and every existing test that drives it from `START` still passes," which is exactly what Step 3 verifies. New router-specific graph tests are Task 10.

- [ ] **Step 1: Rewrite `graph.py`**

```python
# apps/backend/src/main_graph/subgraphs/analysis/graph.py
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.analysis.concern import route_concern
from src.main_graph.subgraphs.analysis.deepagent.nodes import (
    analysis_deepagent_node,
    backstop_dispatch_node,
    coverage_gate,
    route_after_coverage_gate,
)
from src.main_graph.subgraphs.analysis.nodes.run_direct_agents import run_direct_agents
from src.main_graph.subgraphs.analysis.nodes.save_analysis_result import (
    save_analysis_result,
)
from src.main_graph.subgraphs.analysis.nodes.understand_concern import understand_concern
from src.main_graph.subgraphs.analysis.state import AnalysisState


def build_analysis_subgraph():
    builder = StateGraph(AnalysisState)

    builder.add_node("understand_concern", understand_concern)
    builder.add_node("run_direct_agents", run_direct_agents)
    builder.add_node("analysis_deepagent_node", analysis_deepagent_node)
    builder.add_node("coverage_gate", coverage_gate)
    builder.add_node("backstop_dispatch", backstop_dispatch_node)
    builder.add_node("save_analysis_result", save_analysis_result)

    builder.add_edge(START, "understand_concern")
    builder.add_conditional_edges(
        "understand_concern",
        route_concern,
        {"simple": "run_direct_agents", "complex": "analysis_deepagent_node"},
    )
    builder.add_edge("run_direct_agents", "save_analysis_result")
    builder.add_edge("analysis_deepagent_node", "coverage_gate")
    builder.add_conditional_edges("coverage_gate", route_after_coverage_gate)
    builder.add_edge("backstop_dispatch", "save_analysis_result")
    builder.add_edge("save_analysis_result", END)

    return builder.compile()


analysis_subgraph = build_analysis_subgraph()
```

- [ ] **Step 2: Add a `_fake_concern_llm` helper and patch `understand_concern` in all 5 existing tests**

In `apps/backend/tests/subgraphs/test_analysis_subgraph.py`, add this import and helper near the top (alongside the existing `_fake_base_llm`):

```python
from src.main_graph.subgraphs.analysis.concern import Concern
```

```python
def _fake_concern_llm(concern: Concern) -> MagicMock:
    """Mock understand_concern's _llm: with_structured_output(...).ainvoke(...) ->
    the given Concern, every call."""
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=concern
    )
    return mock_llm
```

Then, in each of the 5 existing tests, add one more `patch(...)` to the `with (...)` block for `"src.main_graph.subgraphs.analysis.nodes.understand_concern._llm"`, using `requires_per_dependency_analysis=True` in every case (this preserves each test's originally-exercised `coverage_gate` code path unchanged — see the Global Constraints note on why `coverage_gate` must not be allowed to short-circuit in these pre-existing tests):

`test_analysis_dispatches_agent_and_saves_result` — add:
```python
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    type=["maintenance"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["maintenance_agent"],
                )
            ),
        ),
```

`test_backstop_fires_when_deep_agent_never_delegates` — add:
```python
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    type=["vulnerability"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["vulnerability_agent"],
                )
            ),
        ),
```

`test_analysis_accumulates_bundles_across_two_correction_rounds` — add:
```python
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    type=["maintenance"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["maintenance_agent"],
                )
            ),
        ),
```

`test_parallel_task_calls_in_one_turn_do_not_crash_root_state` — add:
```python
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    type=["maintenance", "supply_chain"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["maintenance_agent", "supply_chain_agent"],
                )
            ),
        ),
```

`test_coverage_gate_skips_per_package_coverage_when_whole_tree_scan_satisfies_concern` — add:
```python
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(
                Concern(
                    type=["vulnerability"],
                    scope="all_dependencies",
                    packages=[],
                    requires_per_dependency_analysis=True,
                    preferred_agents=["vulnerability_agent"],
                )
            ),
        ),
```

- [ ] **Step 3: Run the existing graph-level suite to confirm it's green again**

Requires Docker running (`colima start` if needed). Run: `uv run pytest tests/subgraphs/test_analysis_subgraph.py -v`
Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/graph.py apps/backend/tests/subgraphs/test_analysis_subgraph.py
git commit -m "feat: wire understand_concern/route_concern/run_direct_agents into the analysis subgraph"
```

---

### Task 7: Enforce `DeepAgentLimits` in `subagent_wrapper._run`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py`
- Modify: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py`

**Interfaces:**
- Consumes: `DEEPAGENT_LIMITS`, `SPECIALIST_SEMAPHORE` (Task 3).
- Produces: `_run` now (a) skips dispatch and returns a budget-exhausted message once `len(agent_calls) >= DEEPAGENT_LIMITS.max_specialist_calls`, and (b) acquires `SPECIALIST_SEMAPHORE` around dispatch+run.

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py`:

```python
import asyncio

from langchain_core.messages import AIMessage


@pytest.mark.asyncio
async def test_budget_exhausted_skips_dispatch_and_returns_a_message():
    subagent = build_agent_subagent("maintenance_agent")
    already_used_calls = [
        {"agent_type": "maintenance_agent", "bundle_id": f"b{i}"} for i in range(8)
    ]

    with patch(
        "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
        ".MaintenanceAgent.run"
    ) as mock_run:
        result = await subagent["runnable"].ainvoke(
            {
                "messages": [HumanMessage(content="check chalk")],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "agent_calls": already_used_calls,
            },
            {"configurable": {}},
        )
        mock_run.assert_not_called()

    assert result["bundle_ids"] == []
    assert result["agent_calls"] == []
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert "budget" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_semaphore_caps_concurrent_specialist_calls():
    subagent = build_agent_subagent("maintenance_agent")
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(return_value="bundle-1")
    mock_get_services = MagicMock(
        return_value={"result_dao": fake_dao, "container": MagicMock(), "input_cache": None}
    )

    concurrent = 0
    peak = 0

    async def _slow_run(*args, **kwargs):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return _make_bundle(), ["npm_outdated"], 1

    test_semaphore = asyncio.Semaphore(2)

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(
                return_value=AgentDispatch(
                    domain="maintenance",
                    hypothesis="check chalk",
                    packages_to_focus=["chalk"],
                    agent_type="maintenance_agent",
                )
            ),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper.get_services",
            new=mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper.SPECIALIST_SEMAPHORE",
            test_semaphore,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
            ".MaintenanceAgent.run",
            new=_slow_run,
        ),
    ):
        await asyncio.gather(
            *[
                subagent["runnable"].ainvoke(
                    {
                        "messages": [HumanMessage(content="check chalk")],
                        "job_id": "job-1",
                        "prep_result_id": "prep-1",
                        "agent_calls": [],
                    },
                    {"configurable": {}},
                )
                for _ in range(5)
            ]
        )

    assert peak <= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py -v`
Expected: the two new tests FAIL (budget check and semaphore don't exist yet); the pre-existing 2 tests still pass.

- [ ] **Step 3: Implement budget check and semaphore in `subagent_wrapper.py`**

Update imports (add):

```python
from langchain_core.messages import AIMessage

from src.main_graph.subgraphs.analysis.deepagent.limits import (
    DEEPAGENT_LIMITS,
    SPECIALIST_SEMAPHORE,
)
```

In `_run`, right after the existing whole-tree no-op check and before `task_description = state["messages"][-1].content`, insert:

```python
        if len(agent_calls) >= DEEPAGENT_LIMITS.max_specialist_calls:
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Specialist call budget exhausted "
                            f"({DEEPAGENT_LIMITS.max_specialist_calls} calls "
                            "used). Do not dispatch further specialists -- "
                            "finalize with the evidence already collected, "
                            "prioritizing the highest-risk dependencies, and "
                            "report which packages remain unanalyzed."
                        )
                    )
                ],
                "bundle_ids": [],
                "agent_calls": [],
            }
```

Then wrap dispatch extraction and `run_specialist` in the semaphore:

```python
        task_description = state["messages"][-1].content
        svc = get_services(config)

        async with SPECIALIST_SEMAPHORE:
            dispatch = await _extract_dispatch(task_description, agent_type)
            prep = await svc["result_dao"].get_prep(state["prep_result_id"])
            bundle_id, record = await run_specialist(agent_type, dispatch, prep, svc)

        return {
            "messages": [],
            "bundle_ids": [bundle_id],
            "agent_calls": [record],
        }
```

(`get_services(config)` moves above the semaphore block since it's synchronous and cheap — only the actual specialist work needs to be inside the bounded region.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py apps/backend/tests/unit/subgraphs/analysis/deepagent/test_subagent_wrapper.py
git commit -m "feat: enforce DeepAgentLimits budget and concurrency in subagent_wrapper"
```

---

### Task 8: Rewrite the deep agent prompt

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_prompt.py`

**Interfaces:**
- Consumes: `DEEPAGENT_LIMITS` (Task 3), `state["structured_concern"]` (Task 2).
- Produces: `_SYSTEM_TEMPLATE` rewritten; `analysis_deepagent_node`'s first-round system message now interpolates `concern_type`, `concern_scope`, `max_specialist_calls`, `max_parallel_calls` alongside the existing `roster`/`direct_deps`/`concern`/`context`.

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/tests/unit/subgraphs/analysis/deepagent/test_prompt.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.analysis.deepagent.limits import DEEPAGENT_LIMITS
from src.models.results import PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )


@pytest.mark.asyncio
async def test_first_round_system_message_includes_budget_and_structured_concern():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    captured = {}

    async def _fake_ainvoke(deepagent_state, run_config):
        captured["system_content"] = deepagent_state["messages"][0].content
        return {"bundle_ids": [], "agent_calls": []}

    fake_deep_agent = MagicMock()
    fake_deep_agent.ainvoke = AsyncMock(side_effect=_fake_ainvoke)

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
            mock_get_services,
        ),
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
    ):
        await deepagent_nodes.analysis_deepagent_node(
            {
                "job_id": "job-1",
                "concern": "check whether lodash is maintained",
                "prep_result_id": "prep-1",
                "structured_concern": {
                    "type": ["maintenance"],
                    "scope": "all_dependencies",
                    "packages": [],
                    "requires_per_dependency_analysis": True,
                    "preferred_agents": ["maintenance_agent"],
                },
            },
            {"configurable": {}},
        )

    content = captured["system_content"]
    assert str(DEEPAGENT_LIMITS.max_specialist_calls) in content
    assert str(DEEPAGENT_LIMITS.max_parallel_calls) in content
    assert "type=['maintenance']" in content
    assert "scope=all_dependencies" in content
    assert "Prefer the smallest plan that completely answers the concern" in content
    assert "prioritize the" in content.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_prompt.py -v`
Expected: FAIL — new template text and interpolation keys don't exist yet.

- [ ] **Step 3: Rewrite `_SYSTEM_TEMPLATE` and its interpolation in `nodes.py`**

Replace `_SYSTEM_TEMPLATE` (currently lines 34-62) with:

```python
_SYSTEM_TEMPLATE = textwrap.dedent("""\
    You are a dependency risk investigation agent for a Node.js project. You
    are invoked only for concerns a deterministic router already classified
    as complex -- something a single whole-tree scan cannot fully answer
    alone.

    Your primary goal is to produce a complete answer while minimizing
    specialist invocations. Every specialist call has a cost (latency,
    tokens, rate limits). Prefer the smallest plan that completely answers
    the concern. You have a hard budget of {max_specialist_calls} specialist
    calls, with at most {max_parallel_calls} running concurrently.

    Available specialists (call via the task tool):
    {roster}

    Before delegating any work:
    1. Identify the information required to answer the concern.
    2. Determine the minimum set of specialists needed.
    3. Prefer whole-project specialists over package-level specialists.
    4. Assume the concern is solved after each specialist completes.
    5. Only continue if there is a concrete information gap.

    Whole-project specialists:
    - vulnerability_agent covers vulnerabilities for every dependency.
    - license_agent covers licensing for every dependency.
    Each scans the ENTIRE dependency tree in a single run -- delegate to
    each at most once. If either fully answers the concern, do not invoke
    additional specialists to validate or expand those findings.

    Before dispatching another specialist, ask: "What new information will
    this specialist provide that is necessary for the final report?" If the
    answer is "none" or "only confirmation", stop instead.

    Do not collect evidence simply because it may be interesting. Only
    collect evidence required to answer the user's concern.

    Never use multiple specialists to answer the same question unless the
    previous specialist explicitly left an information gap. For example:
    vulnerability_agent finds known CVEs, then web_research_agent finds the
    same CVEs from GitHub advisories -- this should never happen.

    For every package-scoped specialist you do use, make sure your
    delegated tasks collectively cover every direct dependency relevant to
    the concern -- you may be asked to cover specific missing ones if you
    stop early.

    The investigation is complete when:
    - every required question has evidence;
    - no remaining evidence gap exists;
    - additional specialists would only increase confidence rather than
      change conclusions.
    At that point, stop.

    If answering the concern would exceed your execution budget, prioritize
    the highest-risk dependencies first and report which packages remain
    unanalyzed.

    Direct dependencies (name@installed_version): {direct_deps}
    Concern: {concern} (type={concern_type}, scope={concern_scope})
    Project context: {context}
    """).strip()
```

In `analysis_deepagent_node`, update the import list to add `DEEPAGENT_LIMITS`:

```python
from src.main_graph.subgraphs.analysis.deepagent.limits import DEEPAGENT_LIMITS
```

and update the `if deepagent_state is None:` branch's `system = _SYSTEM_TEMPLATE.format(...)` call to:

```python
        structured_concern = state["structured_concern"]
        system = _SYSTEM_TEMPLATE.format(
            roster=_roster(),
            direct_deps=direct_deps,
            concern=state["concern"],
            concern_type=structured_concern["type"],
            concern_scope=structured_concern["scope"],
            context=prep.discovery_summary[:1000],
            max_specialist_calls=DEEPAGENT_LIMITS.max_specialist_calls,
            max_parallel_calls=DEEPAGENT_LIMITS.max_parallel_calls,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_prompt.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the full deepagent unit test suite to confirm nothing else broke**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/ -v`
Expected: all passed (this only touches system-message text/interpolation, not control flow).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py apps/backend/tests/unit/subgraphs/analysis/deepagent/test_prompt.py
git commit -m "feat: rewrite deep agent prompt around minimal-plan investigation"
```

---

### Task 9: Conditional `coverage_gate` enforcement

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage_gate.py`

**Interfaces:**
- Produces: `coverage_gate` now returns `{"missing_deps": [], "correction_rounds": <n+1>}` immediately, with no bundle fetch and no `whole_tree_scan_satisfies_concern` call, whenever `state["structured_concern"]["requires_per_dependency_analysis"]` is falsy (default `True` if the key or the whole dict is absent).

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage_gate.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.deepagent.nodes import coverage_gate
from src.models.results import PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
        discovery_summary="test",
        vector_store_id="",
    )


def _structured_concern(**overrides) -> dict:
    defaults = dict(
        type=["maintenance"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["maintenance_agent"],
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_short_circuits_when_per_dependency_analysis_not_required():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.get_bundles = AsyncMock(side_effect=AssertionError("must not be called"))
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
            mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes"
            ".whole_tree_scan_satisfies_concern",
            AsyncMock(side_effect=AssertionError("must not be called")),
        ),
    ):
        result = await coverage_gate(
            {
                "job_id": "job-1",
                "concern": "is lodash maintained?",
                "prep_result_id": "prep-1",
                "structured_concern": _structured_concern(
                    requires_per_dependency_analysis=False
                ),
                "agent_calls": [],
            },
            {"configurable": {}},
        )

    assert result["missing_deps"] == []
    assert result["correction_rounds"] == 1


@pytest.mark.asyncio
async def test_still_enforces_when_per_dependency_analysis_required():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.get_bundles = AsyncMock(return_value=[])
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with patch(
        "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
        mock_get_services,
    ):
        result = await coverage_gate(
            {
                "job_id": "job-1",
                "concern": "check every direct dependency for maintenance risk",
                "prep_result_id": "prep-1",
                "structured_concern": _structured_concern(
                    requires_per_dependency_analysis=True
                ),
                "agent_calls": [],
            },
            {"configurable": {}},
        )

    assert result["missing_deps"] == ["lodash"]


@pytest.mark.asyncio
async def test_defaults_to_enforcing_when_structured_concern_is_missing():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.get_bundles = AsyncMock(return_value=[])
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with patch(
        "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
        mock_get_services,
    ):
        result = await coverage_gate(
            {
                "job_id": "job-1",
                "concern": "check maintenance",
                "prep_result_id": "prep-1",
                "agent_calls": [],
            },
            {"configurable": {}},
        )

    assert result["missing_deps"] == ["lodash"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_coverage_gate.py -v`
Expected: the first test FAILs (no short-circuit yet, so the assertion-raising mocks get called); the other two pass already against today's unconditional behavior.

- [ ] **Step 3: Add the short-circuit to `coverage_gate` in `nodes.py`**

At the top of `coverage_gate`, right after the `async def coverage_gate(state: AnalysisState, config: RunnableConfig) -> dict:` line, before `svc = get_services(config)`:

```python
    requires_per_dependency_analysis = (state.get("structured_concern") or {}).get(
        "requires_per_dependency_analysis", True
    )
    if not requires_per_dependency_analysis:
        return {
            "missing_deps": [],
            "correction_rounds": (state.get("correction_rounds") or 0) + 1,
        }
```

(the rest of `coverage_gate`'s body is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/test_coverage_gate.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full deepagent unit test suite to confirm nothing else broke**

Run: `uv run pytest tests/unit/subgraphs/analysis/deepagent/ -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage_gate.py
git commit -m "feat: condition coverage_gate enforcement on requires_per_dependency_analysis"
```

---

### Task 10: Graph-level router tests

**Files:**
- Create: `apps/backend/tests/subgraphs/test_analysis_subgraph_router.py`

**Interfaces:**
- Consumes: everything from Tasks 1-9, `subgraph_config`/`result_dao` fixtures from `tests/subgraphs/conftest.py`.

Requires Docker running (`colima start` if needed) — same as `tests/subgraphs/test_analysis_subgraph.py`.

- [ ] **Step 1: Write the tests**

```python
# apps/backend/tests/subgraphs/test_analysis_subgraph_router.py
"""
Graph-level proof that the router actually routes:

1. A simple concern (vulnerability-only, no per-dependency requirement)
   never reaches analysis_deepagent_node -- if it did, the test would hang
   or error trying to reach a real LLM, since no deep-agent-specific mock
   is installed.
2. A complex concern with requires_per_dependency_analysis=False reaches
   save_analysis_result directly from coverage_gate, even though the deep
   agent never covered the sole direct dependency -- proving the new
   short-circuit (Task 9) is actually wired end-to-end, not just correct
   in isolation.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.analysis.graph import build_analysis_subgraph
from src.models.results import PrepResult


def _seed_prep(job_id: str) -> PrepResult:
    return PrepResult(
        job_id=job_id,
        repo_path="/tmp/test-repo",
        project_metadata={"name": "test-project"},
        manifest_files=["package.json", "package-lock.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
        discovery_summary="test-project depends on lodash.",
        vector_store_id="",
    )


_TRIVY_FIXTURE = {
    "SchemaVersion": 2,
    "Results": [
        {
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2021-23337",
                    "PkgName": "lodash",
                    "InstalledVersion": "4.17.20",
                    "FixedVersion": "4.17.21",
                    "Severity": "HIGH",
                    "Title": "prototype pollution in lodash < 4.17.21",
                    "Description": "Lodash prototype pollution vulnerability",
                    "PrimaryURL": "https://nvd.nist.gov/vuln/detail/CVE-2021-23337",
                }
            ]
        }
    ],
}


def _fake_concern_llm(concern: Concern) -> MagicMock:
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=concern
    )
    return mock_llm


@pytest.mark.asyncio
async def test_simple_concern_skips_deep_agent_entirely(subgraph_config, result_dao):
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_concern = Concern(
        type=["vulnerability"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["vulnerability_agent"],
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(fake_concern),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.graph.analysis_deepagent_node",
            AsyncMock(side_effect=AssertionError("deep agent must not run")),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent"
            ".trivy_vuln_scan",
            AsyncMock(return_value=_TRIVY_FIXTURE),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "check for known CVEs",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert len(analysis.evidence_bundle_ids) == 1
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 1
    assert agent_calls[0]["agent_type"] == "vulnerability_agent"


@pytest.mark.asyncio
async def test_complex_concern_without_per_dependency_requirement_skips_forced_coverage(
    subgraph_config, result_dao
):
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = MagicMock()
    fake_deep_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="No specialists needed, finalizing.")],
            "bundle_ids": [],
            "agent_calls": [],
        }
    )
    fake_concern = Concern(
        type=["maintenance"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["maintenance_agent"],
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _fake_concern_llm(fake_concern),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.graph.backstop_dispatch_node",
            AsyncMock(side_effect=AssertionError("backstop must not run")),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "how healthy is this project's dependency set overall?",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert analysis.evidence_bundle_ids == []
    assert analysis.findings == []
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/subgraphs/test_analysis_subgraph_router.py -v`
Expected: 2 passed.

- [ ] **Step 3: Run the full backend test suite as a final check**

Run: `uv run pytest tests/ -v`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add apps/backend/tests/subgraphs/test_analysis_subgraph_router.py
git commit -m "test: add graph-level coverage for the simple/complex concern router"
```

---

## Self-Review Notes

- **Spec coverage:** section 1 (Concern schema) -> Task 1; section 2 (`understand_concern`) -> Task 2; section 3 (`route_concern`) -> Task 1; section 4 (`run_direct_agents`) -> Task 5; section 5 (`run_specialist`) -> Task 4; section 6 (`DeepAgentLimits`) -> Tasks 3 and 7; section 7 (prompt rewrite) -> Task 8; section 8 (conditional `coverage_gate`) -> Task 9; graph wiring (Goal diagram) -> Task 6; Testing section -> Tasks 1-2, 4-10 each carry their own tests, plus Task 10 for the graph-level router proof the spec's Testing section calls for.
- **Placeholder scan:** no `TODO`/`TBD` in any task; every step has real, complete code.
- **Type consistency:** `run_specialist(agent_type: str, dispatch: AgentDispatch, prep: PrepResult, svc: PipelineConfigurable) -> tuple[str, dict]` (Task 4) is the exact signature Tasks 5 and 7 call. `Concern` fields (`type`, `scope`, `packages`, `requires_per_dependency_analysis`, `preferred_agents`) are identical across Tasks 1, 2, 5, 6, 9, 10. `DEEPAGENT_LIMITS`/`SPECIALIST_SEMAPHORE` (Task 3) are imported by name, unchanged, in Tasks 5, 7, 8.
- **Known deferred verification:** Task 7's budget-exhausted message assumes a `CompiledSubAgent`'s returned `messages` surfaces as the `task()` tool's result text to the root deep agent's LLM (flagged as an open question in spec section 6). The test in Task 7 verifies the message is correctly *produced*; it does not verify `deepagents==0.6.12` actually *surfaces* it to the root LLM, since that would require exercising the full deepagents internals rather than `_run` in isolation. If this turns out not to hold, the root agent would simply not see a budget-exhausted explanation and would need to infer completion from getting no new evidence back -- worth a manual smoke-test run against a real job before considering this fully verified.
