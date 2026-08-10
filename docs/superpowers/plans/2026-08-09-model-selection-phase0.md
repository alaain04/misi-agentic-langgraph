# Model Selection Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LLM model choice configurable per agent role (not hardcoded per call site) and attribute cost, tokens, call count, and latency to each role — the two prerequisites `apps/backend/docs/model-selection.md` (sections 6.1-6.3) requires before any per-agent model comparison can be run or trusted.

**Architecture:** A new `AgentRole` enum and `src/utils/model_registry.py` centralize "which `Model` backs which role", resolved from `settings.model_overrides` (empty by default — every role still gets the one current default, zero behavior change on merge). Every LLM construction site tags its runnable with `agent_role:<role>` via `.with_config(tags=[...])`. `CostCallback` (already the single object threaded through every graph run via LangChain's callback system) reads those tags off `on_chat_model_start`/`on_llm_end` and accumulates a per-role breakdown alongside the existing scalar total, so cost/token/latency attribution requires no per-call-site `time.monotonic()` bookkeeping (a deliberate simplification vs. the doc's literal 6.3 wording, which predates this callback-tag mechanism being available). The breakdown is persisted onto `Job` at job completion and returned from the status endpoint, so a Phase 0 baseline run can be inspected without a DB query.

**Tech Stack:** Python 3.12, LangChain/LangGraph, pydantic-settings, pytest (+ `langchain_core.language_models.fake_chat_models.GenericFakeChatModel` for callback tests, no network).

## Global Constraints

- Zero behavior change on merge: every role's default model stays `Model.GPT_5_4_MINI` (the current uniform default) unless `settings.model_overrides` names it.
- No new dependency: everything is built on `langchain_core` primitives already in use (`BaseCallbackHandler`, `Runnable.with_config`).
- Follow existing code conventions: flat pytest functions (no test classes), `from __future__ import annotations` only where the file already uses it, no docstring bloat — see `apps/backend/docs/code-conventions.md`.
- Run `uv run pytest` (backend dir) and `uv run mypy src` (if configured) before each commit's "done" claim, per project CLAUDE.md.
- Never touch `docs/model-selection.md` sections 7-9 (execution plan / decision records / risks) in this round — this plan only lands the enabling work (section 6.1-6.3), not the actual model comparison (Phase 1-3 of section 7).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/utils/model_registry.py` (new) | `AgentRole` enum, `resolve_model(role)`, `get_role_llm(role, ...)` — the one place role→model resolution happens. |
| `src/utils/config.py` (modify) | Add `model_overrides: dict[str, str] = {}` to `Settings`. |
| `src/utils/cost.py` (modify) | `CostCallback` gains `on_chat_model_start`/`on_llm_start` timing hooks and a `breakdown()` method returning per-role `RoleUsage` (cost, tokens, call_count, latency_ms). Existing `.cost()`/`.total_tokens` behavior unchanged. |
| 13 call-site files (modify) | Replace `get_llm(Model.GPT_5_4_MINI, ...)` with `get_role_llm(AgentRole.X, ...)`. No other logic changes. |
| `src/models/job.py` (modify) | Add `Job.cost_breakdown: dict | None = None`. |
| `src/domain/ports/job_repository_port.py` + `src/services/job_dao.py` (modify) | Add `save_cost_breakdown(job_id, breakdown: dict) -> None`. |
| `src/services/job_runner.py` (modify) | Call `save_cost_breakdown` alongside the existing `save_cost` calls. |
| `src/api/schemas.py` (modify) | Add `cost_breakdown: dict | None = None` to `AnalysisStatusResponse`. |
| `src/api/routes.py` (modify) | Thread `job.cost_breakdown` into the status response, same pattern as `job.cost`. |

---

## Task 1: `AgentRole` enum and model registry

**Files:**
- Create: `apps/backend/src/utils/model_registry.py`
- Modify: `apps/backend/src/utils/config.py`
- Test: `apps/backend/tests/unit/utils/test_model_registry.py`

**Interfaces:**
- Produces: `AgentRole` (StrEnum, 14 members, values below), `resolve_model(role: AgentRole) -> Model`, `get_role_llm(role: AgentRole, *, rate_limiter: BaseRateLimiter | None = None, max_retries: int | None = None) -> BaseChatModel` (returns a runnable already tagged `f"agent_role:{role.value}"` via `.with_config`).

`AgentRole` members (value = doc's table role name, one per current call site):

```
UNDERSTAND_CONCERN = "understand_concern"
ANALYSIS_ROOT_DEEPAGENT = "analysis_root_deepagent"
ANALYSIS_DISPATCH = "analysis_dispatch"
COVERAGE_JUDGE = "coverage_judge"
SPECIALIST_AGENT = "specialist_agent"
ANALYSIS_CRITIQUE = "analysis_critique"
REPORT_SYNTHESIZER = "report_synthesizer"
FINDING_ENRICHER = "finding_enricher"
IMPACT_ANALYSIS = "impact_analysis"
REPORT_CRITIQUE = "report_critique"
REMEDIATION_CLASSIFY = "remediation_classify"
REMEDIATION_INVESTIGATE = "remediation_investigate"
REMEDIATION_PLAN = "remediation_plan"
REMEDIATION_EXECUTION_DEEPAGENT = "remediation_execution_deepagent"
```

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/tests/unit/utils/test_model_registry.py
from src.utils.llm import Model
from src.utils.model_registry import AgentRole, get_role_llm, resolve_model
from src.utils.config import settings


def test_resolve_model_defaults_to_gpt_5_4_mini_for_every_role():
    for role in AgentRole:
        assert resolve_model(role) is Model.GPT_5_4_MINI


def test_resolve_model_honors_override(monkeypatch):
    monkeypatch.setattr(
        settings,
        "model_overrides",
        {"specialist_agent": "gpt-5.4-nano-2026-03-17"},
    )
    assert resolve_model(AgentRole.SPECIALIST_AGENT) is Model.GPT_5_4_NANO
    assert resolve_model(AgentRole.COVERAGE_JUDGE) is Model.GPT_5_4_MINI


def test_resolve_model_rejects_unknown_override_value(monkeypatch):
    monkeypatch.setattr(
        settings, "model_overrides", {"specialist_agent": "not-a-real-model"}
    )
    import pytest

    with pytest.raises(ValueError):
        resolve_model(AgentRole.SPECIALIST_AGENT)


def test_get_role_llm_tags_the_runnable_with_its_role():
    llm = get_role_llm(AgentRole.REMEDIATION_PLAN)
    assert "agent_role:remediation_plan" in llm.config.get("tags", [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/utils/test_model_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.utils.model_registry'`

- [ ] **Step 3: Add `model_overrides` to `Settings`**

In `apps/backend/src/utils/config.py`, add after `license_lookup_concurrency`:

```python
    # Per-role LLM model overrides (docs/model-selection.md). Maps an
    # AgentRole value to a Model value, e.g.
    # MODEL_OVERRIDES='{"specialist_agent": "gpt-5.4-nano-2026-03-17"}'.
    # Empty by default: every role uses the one default model until a role
    # earns a documented deviation (docs/model-selection.md section 8).
    model_overrides: dict[str, str] = {}
```

- [ ] **Step 4: Write `model_registry.py`**

```python
"""Per-role LLM resolution: the one place that decides which Model backs
each AgentRole. Call sites ask for a role, never a literal Model, so a
comparison experiment is a settings/env change, not a source edit — see
docs/model-selection.md section 6.1.
"""

from enum import StrEnum

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import BaseRateLimiter

from src.utils.config import settings
from src.utils.llm import Model, get_llm


class AgentRole(StrEnum):
    UNDERSTAND_CONCERN = "understand_concern"
    ANALYSIS_ROOT_DEEPAGENT = "analysis_root_deepagent"
    ANALYSIS_DISPATCH = "analysis_dispatch"
    COVERAGE_JUDGE = "coverage_judge"
    SPECIALIST_AGENT = "specialist_agent"
    ANALYSIS_CRITIQUE = "analysis_critique"
    REPORT_SYNTHESIZER = "report_synthesizer"
    FINDING_ENRICHER = "finding_enricher"
    IMPACT_ANALYSIS = "impact_analysis"
    REPORT_CRITIQUE = "report_critique"
    REMEDIATION_CLASSIFY = "remediation_classify"
    REMEDIATION_INVESTIGATE = "remediation_investigate"
    REMEDIATION_PLAN = "remediation_plan"
    REMEDIATION_EXECUTION_DEEPAGENT = "remediation_execution_deepagent"


# One default model, applied everywhere by policy — the "no differentiation
# to justify yet" baseline from docs/model-selection.md section 2. Change
# this only with a decision record (section 8); change a single role via
# settings.model_overrides instead.
_DEFAULT_MODEL = Model.GPT_5_4_MINI


def resolve_model(role: AgentRole) -> Model:
    override = settings.model_overrides.get(role.value)
    if override is None:
        return _DEFAULT_MODEL
    return Model(override)  # raises ValueError loudly on a typo'd override


def get_role_llm(
    role: AgentRole,
    *,
    rate_limiter: BaseRateLimiter | None = None,
    max_retries: int | None = None,
) -> BaseChatModel:
    llm = get_llm(
        resolve_model(role), rate_limiter=rate_limiter, max_retries=max_retries
    )
    return llm.with_config(tags=[f"agent_role:{role.value}"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/utils/test_model_registry.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/utils/model_registry.py apps/backend/src/utils/config.py apps/backend/tests/unit/utils/test_model_registry.py
git commit -m "feat: add per-role model registry (docs/model-selection.md 6.1)"
```

---

## Task 2: Per-role cost/latency breakdown in `CostCallback`

**Files:**
- Modify: `apps/backend/src/utils/cost.py`
- Test: `apps/backend/tests/unit/utils/test_cost.py` (new file)

**Interfaces:**
- Consumes: `AgentRole` tags of the form `agent_role:<value>` set by `get_role_llm` (Task 1). A call with no such tag buckets under `"untagged"`.
- Produces: `CostCallback.breakdown() -> dict[str, dict]` where each value has keys `cost` (float, USD), `prompt_tokens` (int), `completion_tokens` (int), `call_count` (int), `latency_ms` (float, summed). Existing `CostCallback.cost()`, `.total_tokens`, `.prompt_tokens`, `.completion_tokens` keep their current meaning (unchanged, still the all-roles total) — nothing else in the codebase that reads them needs to change.

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/tests/unit/utils/test_cost.py
import asyncio

from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from src.utils.cost import CostCallback


def _fake_llm_with_usage(role: str, prompt_tokens: int, completion_tokens: int):
    msg = AIMessage(
        content="ok",
        response_metadata={
            "token_usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            "model_name": "gpt-5.4-mini-2026-03-17",
        },
    )
    return GenericFakeChatModel(messages=iter([msg])).with_config(
        tags=[f"agent_role:{role}"]
    )


def test_breakdown_buckets_cost_and_tokens_by_role_tag():
    cb = CostCallback()
    llm_a = _fake_llm_with_usage("specialist_agent", 1000, 500)
    llm_b = _fake_llm_with_usage("coverage_judge", 2000, 1000)

    asyncio.run(llm_a.ainvoke("hi", config={"callbacks": [cb]}))
    asyncio.run(llm_b.ainvoke("hi", config={"callbacks": [cb]}))

    breakdown = cb.breakdown()
    assert set(breakdown) == {"specialist_agent", "coverage_judge"}
    assert breakdown["specialist_agent"]["prompt_tokens"] == 1000
    assert breakdown["specialist_agent"]["completion_tokens"] == 500
    assert breakdown["specialist_agent"]["call_count"] == 1
    assert breakdown["specialist_agent"]["cost"] > 0
    assert breakdown["coverage_judge"]["prompt_tokens"] == 2000


def test_breakdown_sums_multiple_calls_for_the_same_role():
    cb = CostCallback()
    llm = _fake_llm_with_usage("remediation_plan", 100, 50)
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    llm2 = _fake_llm_with_usage("remediation_plan", 100, 50)
    asyncio.run(llm2.ainvoke("hi", config={"callbacks": [cb]}))

    assert cb.breakdown()["remediation_plan"]["call_count"] == 2
    assert cb.breakdown()["remediation_plan"]["prompt_tokens"] == 200


def test_breakdown_buckets_untagged_calls_separately():
    cb = CostCallback()
    llm = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="ok",
                    response_metadata={
                        "token_usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                        },
                        "model_name": "gpt-5.4-mini-2026-03-17",
                    },
                )
            ]
        )
    )
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    assert "untagged" in cb.breakdown()


def test_breakdown_records_latency_ms_per_role():
    cb = CostCallback()
    llm = _fake_llm_with_usage("understand_concern", 10, 5)
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    assert cb.breakdown()["understand_concern"]["latency_ms"] >= 0


def test_total_cost_and_tokens_unchanged_by_breakdown_tracking():
    cb = CostCallback()
    llm = _fake_llm_with_usage("specialist_agent", 1000, 500)
    asyncio.run(llm.ainvoke("hi", config={"callbacks": [cb]}))
    assert cb.total_tokens == 1500
    assert cb.cost() > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/utils/test_cost.py -v`
Expected: FAIL with `AttributeError: 'CostCallback' object has no attribute 'breakdown'`

- [ ] **Step 3: Implement the breakdown tracking**

Replace the full contents of `apps/backend/src/utils/cost.py`:

```python
"""LLM cost tracking via LangChain callback."""

import time
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

# USD per 1M tokens: {model: (input_rate, output_rate)}
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-5.4-nano-2026-03-17": (0.10, 0.40),
    "gpt-5.4-mini-2026-03-17": (0.40, 1.60),
    "gpt-5.5-2026-04-23": (2.50, 10.00),
}
_FALLBACK_RATE = (0.40, 1.60)
_ROLE_TAG_PREFIX = "agent_role:"
_UNTAGGED = "untagged"


def _role_from_tags(tags: list[str] | None) -> str:
    for tag in tags or []:
        if tag.startswith(_ROLE_TAG_PREFIX):
            return tag[len(_ROLE_TAG_PREFIX) :]
    return _UNTAGGED


class CostCallback(BaseCallbackHandler):
    """Accumulates token usage and computes USD cost across all LLM calls.

    Also keys usage by the calling AgentRole (via the `agent_role:<role>`
    tag `get_role_llm` binds on every LLM runnable — see
    src/utils/model_registry.py) so cost/latency can be attributed per role,
    not just summed globally. Calls with no such tag bucket under
    "untagged".
    """

    def __init__(self) -> None:
        super().__init__()
        self._cost: float = 0.0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self._breakdown: dict[str, dict] = {}
        self._start_times: dict[UUID, float] = {}

    def on_llm_start(self, serialized, prompts, *, run_id: UUID, **kwargs) -> None:
        self._start_times[run_id] = time.monotonic()

    def on_chat_model_start(
        self, serialized, messages, *, run_id: UUID, **kwargs
    ) -> None:
        self._start_times[run_id] = time.monotonic()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        **kwargs,
    ) -> None:
        usage = (response.llm_output or {}).get("token_usage", {})
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        model = (response.llm_output or {}).get("model_name", "")
        input_rate, output_rate = _PRICING.get(model, _FALLBACK_RATE)
        call_cost = (prompt * input_rate + completion * output_rate) / 1_000_000

        self._cost += call_cost
        self.prompt_tokens += prompt
        self.completion_tokens += completion

        start = self._start_times.pop(run_id, None)
        latency_ms = (time.monotonic() - start) * 1000 if start is not None else 0.0

        role = _role_from_tags(tags)
        bucket = self._breakdown.setdefault(
            role,
            {
                "cost": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "call_count": 0,
                "latency_ms": 0.0,
            },
        )
        bucket["cost"] += call_cost
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion
        bucket["call_count"] += 1
        bucket["latency_ms"] += latency_ms

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost(self) -> float:
        return round(self._cost, 6)

    def breakdown(self) -> dict[str, dict]:
        """Per-role usage snapshot. Rounds cost/latency for readability;
        callers needing full precision should read `.cost()` for the total."""
        return {
            role: {
                "cost": round(b["cost"], 6),
                "prompt_tokens": b["prompt_tokens"],
                "completion_tokens": b["completion_tokens"],
                "call_count": b["call_count"],
                "latency_ms": round(b["latency_ms"], 1),
            }
            for role, b in self._breakdown.items()
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/utils/test_cost.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full existing cost-adjacent test suite to check for regressions**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_runner.py -v`
Expected: PASS (no behavior change to `.cost()`/`.total_tokens`, which is all `job_runner.py` currently reads)

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/utils/cost.py apps/backend/tests/unit/utils/test_cost.py
git commit -m "feat: attribute LLM cost, tokens, and latency per agent role (docs/model-selection.md 6.2-6.3)"
```

---

## Task 3: Migrate analysis subgraph call sites to `get_role_llm`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/nodes/understand_concern.py:22`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py:84`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/subagent_wrapper.py:33`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/coverage.py:50`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/base_agent.py:29,34`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/critique.py:12`
- Test: `apps/backend/tests/unit/subgraphs/analysis/test_model_role_tagging.py` (new file)

**Interfaces:**
- Consumes: `AgentRole`, `get_role_llm` from Task 1.
- Produces: nothing new — same module-level `_llm` / `_deep_agent` names each file already exposes, just constructed via the registry instead of a hardcoded `Model.GPT_5_4_MINI` literal.

For each of the 6 files, apply the same two-line change: swap the import and swap the construction call. Example for `understand_concern.py`:

```python
# before
from src.utils.llm import Model, get_llm
...
_llm = get_llm(Model.GPT_5_4_MINI)

# after
from src.utils.model_registry import AgentRole, get_role_llm
...
_llm = get_role_llm(AgentRole.UNDERSTAND_CONCERN)
```

Apply the matching role to each site:

| File:line | Role |
|---|---|
| `nodes/understand_concern.py:22` | `AgentRole.UNDERSTAND_CONCERN` |
| `deepagent/nodes.py:84` (inside `_build_deep_agent`, `model=get_llm(Model.GPT_5_4_MINI)`) | `AgentRole.ANALYSIS_ROOT_DEEPAGENT` |
| `deepagent/subagent_wrapper.py:33` | `AgentRole.ANALYSIS_DISPATCH` |
| `deepagent/coverage.py:50` | `AgentRole.COVERAGE_JUDGE` |
| `agents/base_agent.py:34` | `AgentRole.SPECIALIST_AGENT` |
| `agents/critique.py:12` | `AgentRole.ANALYSIS_CRITIQUE` |

For `deepagent/nodes.py`, only the `model=get_llm(Model.GPT_5_4_MINI)` argument inside `_build_deep_agent()` changes to `model=get_role_llm(AgentRole.ANALYSIS_ROOT_DEEPAGENT)`; the `from src.utils.llm import Model, get_llm` import at the top of that file is replaced with `from src.utils.model_registry import AgentRole, get_role_llm`.

- [ ] **Step 1: Write the failing test**

This test imports each module and asserts its module-level LLM object carries the expected role tag — it catches a copy-paste mistake (wrong role assigned to a file) that a plain "does it still import" check would miss.

```python
# apps/backend/tests/unit/subgraphs/analysis/test_model_role_tagging.py
import importlib


def _tags_of(module_path: str, attr: str) -> list[str]:
    module = importlib.import_module(module_path)
    obj = getattr(module, attr)
    return obj.config.get("tags", [])


def test_understand_concern_tagged_correctly():
    tags = _tags_of(
        "src.main_graph.subgraphs.analysis.nodes.understand_concern", "_llm"
    )
    assert "agent_role:understand_concern" in tags


def test_analysis_dispatch_tagged_correctly():
    tags = _tags_of(
        "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper", "_llm"
    )
    assert "agent_role:analysis_dispatch" in tags


def test_coverage_judge_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.analysis.deepagent.coverage", "_llm")
    assert "agent_role:coverage_judge" in tags


def test_specialist_agent_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.analysis.agents.base_agent", "_llm")
    assert "agent_role:specialist_agent" in tags


def test_analysis_critique_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.analysis.agents.critique", "_llm")
    assert "agent_role:analysis_critique" in tags
```

Note: `deepagent/nodes.py`'s `_deep_agent` is a compiled `create_deep_agent` graph, not a bare chat-model runnable, so its role tag isn't directly introspectable the same way — it's covered by Step 5's import smoke check instead of a tag assertion.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/test_model_role_tagging.py -v`
Expected: FAIL (files still import `get_llm`/`Model` directly, `_llm` has no `agent_role` tag)

- [ ] **Step 3: Apply the 6 file edits**

Make the import + construction swap described above in each of the 6 files, using the role table.

- [ ] **Step 4: Run the new test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/test_model_role_tagging.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full analysis subgraph test suite to check nothing else broke**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/ tests/subgraphs/ -v -k analysis`
Expected: PASS — these tests mock/stub the LLM boundary already, so swapping the construction call underneath should not change their assertions. If any test patches `src.main_graph.subgraphs.analysis.agents.base_agent.get_llm` (or similar) directly, update the patch target to `get_role_llm` in that same file (do not change the test's assertions, only the patch target).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis apps/backend/tests/unit/subgraphs/analysis/test_model_role_tagging.py
git commit -m "refactor: route analysis subgraph LLM calls through the role registry"
```

---

## Task 4: Migrate report subgraph call sites to `get_role_llm`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/report/nodes/report_synthesizer.py:15`
- Modify: `apps/backend/src/main_graph/subgraphs/report/agents/finding_enricher_agent.py:33`
- Modify: `apps/backend/src/main_graph/subgraphs/report/agents/impact_analysis_agent.py:26`
- Modify: `apps/backend/src/main_graph/subgraphs/report/agents/critique.py:12`
- Test: `apps/backend/tests/unit/subgraphs/report/test_model_role_tagging.py` (new file)

**Interfaces:**
- Consumes: `AgentRole`, `get_role_llm` from Task 1.
- Produces: same as Task 3 — module-level names unchanged, construction swapped.

| File:line | Role |
|---|---|
| `report/nodes/report_synthesizer.py:15` | `AgentRole.REPORT_SYNTHESIZER` |
| `report/agents/finding_enricher_agent.py:33` | `AgentRole.FINDING_ENRICHER` |
| `report/agents/impact_analysis_agent.py:26` | `AgentRole.IMPACT_ANALYSIS` |
| `report/agents/critique.py:12` | `AgentRole.REPORT_CRITIQUE` |

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/tests/unit/subgraphs/report/test_model_role_tagging.py
import importlib


def _tags_of(module_path: str, attr: str = "_llm") -> list[str]:
    module = importlib.import_module(module_path)
    return getattr(module, attr).config.get("tags", [])


def test_report_synthesizer_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.nodes.report_synthesizer")
    assert "agent_role:report_synthesizer" in tags


def test_finding_enricher_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.agents.finding_enricher_agent")
    assert "agent_role:finding_enricher" in tags


def test_impact_analysis_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.agents.impact_analysis_agent")
    assert "agent_role:impact_analysis" in tags


def test_report_critique_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.report.agents.critique")
    assert "agent_role:report_critique" in tags
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/report/test_model_role_tagging.py -v`
Expected: FAIL

- [ ] **Step 3: Apply the 4 file edits** (same import + construction swap pattern as Task 3)

- [ ] **Step 4: Run the new test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/report/test_model_role_tagging.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full report subgraph test suite**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/report/ -v`
Expected: PASS (update any `get_llm` patch targets to `get_role_llm`, same caveat as Task 3 Step 5)

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/report apps/backend/tests/unit/subgraphs/report/test_model_role_tagging.py
git commit -m "refactor: route report subgraph LLM calls through the role registry"
```

---

## Task 5: Migrate remediation subgraph call sites to `get_role_llm`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/classify.py:31`
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/investigate.py:27`
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/plan.py:20`
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/subagent_wrapper.py:80`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_model_role_tagging.py` (new file)

**Interfaces:**
- Consumes: `AgentRole`, `get_role_llm` from Task 1. `deepagent/subagent_wrapper.py`'s `build_execution_agent` currently calls `get_llm(Model.GPT_5_4_MINI, rate_limiter=REMEDIATION_RATE_LIMITER, max_retries=MAX_RETRIES)` — `get_role_llm` accepts the same `rate_limiter`/`max_retries` kwargs, so only the first positional argument and the import change.
- Produces: same pattern as Tasks 3-4.

| File:line | Role |
|---|---|
| `remediation/classify.py:31` | `AgentRole.REMEDIATION_CLASSIFY` |
| `remediation/investigate.py:27` | `AgentRole.REMEDIATION_INVESTIGATE` |
| `remediation/plan.py:20` | `AgentRole.REMEDIATION_PLAN` |
| `remediation/deepagent/subagent_wrapper.py:80` (`build_execution_agent`) | `AgentRole.REMEDIATION_EXECUTION_DEEPAGENT` |

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/tests/unit/subgraphs/remediation/test_model_role_tagging.py
import importlib


def _tags_of(module_path: str, attr: str = "_llm") -> list[str]:
    module = importlib.import_module(module_path)
    return getattr(module, attr).config.get("tags", [])


def test_remediation_classify_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.classify")
    assert "agent_role:remediation_classify" in tags


def test_remediation_investigate_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.investigate")
    assert "agent_role:remediation_investigate" in tags


def test_remediation_plan_tagged_correctly():
    tags = _tags_of("src.main_graph.subgraphs.remediation.plan")
    assert "agent_role:remediation_plan" in tags


def test_remediation_execution_deepagent_tagged_correctly():
    from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
        build_execution_agent,
    )
    from src.domain.ports.container_run_port import ContainerRunPort

    class _FakeContainer(ContainerRunPort):
        async def run(self, *args, **kwargs):
            raise NotImplementedError

    agent = build_execution_agent(
        work_dir="/tmp/does-not-need-to-exist",
        container=_FakeContainer(),
        docker_image="irrelevant:latest",
        package_manager="npm",
    )
    assert agent is not None
```

The last test only checks `build_execution_agent` still constructs successfully with the new call — the underlying model is wrapped inside `create_deep_agent`'s compiled graph, so its tag isn't directly introspectable the same way as a bare `_llm`; correctness there is instead verified end-to-end in Task 6.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_model_role_tagging.py -v`
Expected: FAIL (first 3 tests: `AttributeError` — no `agent_role` tag yet)

- [ ] **Step 3: Apply the 4 file edits** (same import + construction swap pattern; for `subagent_wrapper.py`, only swap `get_llm(Model.GPT_5_4_MINI, rate_limiter=..., max_retries=...)` to `get_role_llm(AgentRole.REMEDIATION_EXECUTION_DEEPAGENT, rate_limiter=..., max_retries=...)`)

- [ ] **Step 4: Run the new test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_model_role_tagging.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full remediation subgraph test suite**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/ tests/subgraphs/test_remediation_subgraph.py -v`
Expected: PASS (update any `get_llm` patch targets to `get_role_llm`, same caveat as Task 3 Step 5)

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation apps/backend/tests/unit/subgraphs/remediation/test_model_role_tagging.py
git commit -m "refactor: route remediation subgraph LLM calls through the role registry"
```

---

## Task 6: Persist and expose the per-role cost breakdown on `Job`

**Files:**
- Modify: `apps/backend/src/models/job.py`
- Modify: `apps/backend/src/domain/ports/job_repository_port.py`
- Modify: `apps/backend/src/services/job_dao.py`
- Modify: `apps/backend/src/services/job_runner.py`
- Modify: `apps/backend/src/api/schemas.py`
- Modify: `apps/backend/src/api/routes.py`
- Test: `apps/backend/tests/unit/services/test_job_dao_cost_breakdown.py` (new file)
- Test: modify `apps/backend/tests/unit/services/test_job_runner.py`

**Interfaces:**
- Consumes: `CostCallback.breakdown()` from Task 2.
- Produces: `Job.cost_breakdown: dict | None`, `JobRepositoryPort.save_cost_breakdown(job_id: str, breakdown: dict) -> None`, `AnalysisStatusResponse.cost_breakdown: dict | None`.

**Convention note:** this codebase does not unit-test `JobDAO`'s Mongo-backed methods directly — `save_cost` (the method `save_cost_breakdown` mirrors) has no dedicated test at all. DAO methods are verified either as port-interface presence checks (see `tests/unit/services/test_job_dao_push_artifact_message.py`, which asserts a method name is a member of `JobRepositoryPort` via `inspect.getmembers`) or exercised end-to-end through Docker-based `testcontainers` integration tests (see `tests/subgraphs/conftest.py`) for DAOs that do get that treatment (`ResultDAO`, not `JobDAO`). Follow the port-interface-check convention here — do not introduce a new mongomock/testcontainer dependency for this one method, that would be inconsistent with how every other `JobDAO` method is (or isn't) tested in this repo.

- [ ] **Step 1: Write the failing test for the port method**

```python
# apps/backend/tests/unit/services/test_job_dao_cost_breakdown.py
import inspect

from src.domain.ports.job_repository_port import JobRepositoryPort


def test_save_cost_breakdown_is_on_port():
    members = {name for name, _ in inspect.getmembers(JobRepositoryPort)}
    assert "save_cost_breakdown" in members
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_dao_cost_breakdown.py -v`
Expected: FAIL — `save_cost_breakdown` is not yet a member of `JobRepositoryPort`

- [ ] **Step 3: Add the field, port method, and DAO implementation**

In `apps/backend/src/models/job.py`, add to `Job` right after the existing `cost` field:

```python
    cost_breakdown: dict | None = None
```

In `apps/backend/src/domain/ports/job_repository_port.py`, add after the `save_cost` abstract method:

```python
    @abstractmethod
    async def save_cost_breakdown(self, job_id: str, breakdown: dict) -> None: ...
```

In `apps/backend/src/services/job_dao.py`, add right after `save_cost`:

```python
    async def save_cost_breakdown(self, job_id: str, breakdown: dict) -> None:
        await self._col.update_one(
            {"_id": job_id}, {"$set": {"cost_breakdown": breakdown}}
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_dao_cost_breakdown.py -v`
Expected: PASS

- [ ] **Step 5: Wire `save_cost_breakdown` into `job_runner.py`**

In `apps/backend/src/services/job_runner.py`, both `run_analysis` and `resume_analysis` call `await dao.save_cost(job_id, cost_cb.cost())` in three places total (success path + two exception-handler paths across the two functions). Add an adjacent `await dao.save_cost_breakdown(job_id, cost_cb.breakdown())` next to every one of those calls, so the breakdown is persisted whenever the total is, including on failure (a failed run's partial spend-by-role is exactly the kind of thing Phase 0 wants visible).

Example for the success path in `run_analysis` (apply the same pairing at all three call sites):

```python
        await dao.save_cost(job_id, cost_cb.cost())
        await dao.save_cost_breakdown(job_id, cost_cb.breakdown())
```

- [ ] **Step 6: Write/extend the job_runner test**

Open `apps/backend/tests/unit/services/test_job_runner.py`, find the existing test(s) asserting `dao.save_cost` was called (there should be at least one per success/failure path, given `save_cost` already has this exact three-call-site pattern). Add a parallel assertion next to each: `dao.save_cost_breakdown.assert_called_once_with(job_id, cost_cb.breakdown())` (or whatever the existing test's mock/assert idiom is — match it, don't invent a new one).

- [ ] **Step 7: Run job_runner tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/services/test_job_runner.py -v`
Expected: PASS

- [ ] **Step 8: Expose `cost_breakdown` on the status response**

In `apps/backend/src/api/schemas.py`, add to `AnalysisStatusResponse` right after `cost: float | None = None`:

```python
    cost_breakdown: dict | None = None
```

In `apps/backend/src/api/routes.py`, find where `AnalysisStatusResponse(... cost=job.cost ...)` is constructed (same handler that builds the status response from a `Job`) and add `cost_breakdown=job.cost_breakdown` alongside `cost=job.cost`.

- [ ] **Step 9: Run the routes test suite**

Run: `cd apps/backend && uv run pytest tests/unit/test_routes.py -v`
Expected: PASS. If an existing test asserts the exact shape of the status response dict (e.g. comparing full JSON), add `cost_breakdown` to its expected payload rather than loosening the assertion.

- [ ] **Step 10: Commit**

```bash
git add apps/backend/src/models/job.py apps/backend/src/domain/ports/job_repository_port.py apps/backend/src/services/job_dao.py apps/backend/src/services/job_runner.py apps/backend/src/api/schemas.py apps/backend/src/api/routes.py apps/backend/tests/unit/services/test_job_dao_cost_breakdown.py apps/backend/tests/unit/services/test_job_runner.py apps/backend/tests/unit/test_routes.py
git commit -m "feat: persist and expose per-role cost/latency breakdown on Job"
```

---

## Task 7: Full-suite verification and doc update

**Files:**
- Modify: `apps/backend/docs/model-selection.md`

- [ ] **Step 1: Run the entire backend test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: PASS, zero regressions. Every LLM-constructing module now imports `get_role_llm`/`AgentRole` instead of `get_llm`/`Model` directly — grep to confirm no call site was missed:

Run: `cd apps/backend && grep -rn "get_llm(Model\." src/main_graph/`
Expected: no output (every call site migrated in Tasks 3-5; `src/utils/llm.py` and `src/utils/model_registry.py` themselves are the only remaining `get_llm` callers, which is correct — they're the factory, not a consumer).

- [ ] **Step 2: Run lint/typecheck if configured**

Run: `cd apps/backend && uv run ruff check src/ tests/` (or whatever this repo's actual lint command is — check `apps/backend/pyproject.toml` / `Makefile` / CI config for the exact invocation if `ruff` is not it)
Expected: no new errors introduced by this plan's files.

- [ ] **Step 3: Manually verify a settings override actually swaps the model**

Run: `cd apps/backend && MODEL_OVERRIDES='{"specialist_agent": "gpt-5.4-nano-2026-03-17"}' uv run python -c "
from src.utils.model_registry import AgentRole, resolve_model
print(resolve_model(AgentRole.SPECIALIST_AGENT))
print(resolve_model(AgentRole.COVERAGE_JUDGE))
"`
Expected: first line prints `Model.GPT_5_4_NANO`'s value (`gpt-5.4-nano-2026-03-17`), second prints the untouched default (`gpt-5.5-2026-04-23`... no — `gpt-5.4-mini-2026-03-17`, the current `_DEFAULT_MODEL`). This is the concrete proof that "implementing the correct model in each task" (the user's original ask) now works via one env var, with no source edit and no per-file hunting.

- [ ] **Step 4: Update `docs/model-selection.md` section 1 and section 6**

In section "1. Current state", update the claim "All 14 pass `Model.GPT_5_4_MINI`" and "Model choice is bound at import time... a model cannot be swapped by configuration" — both are now false. Replace with a short note: model choice is now resolved per `AgentRole` via `src/utils/model_registry.py`, overridable through `settings.model_overrides` (env var `MODEL_OVERRIDES`, JSON-encoded), still defaulting uniformly to `GPT_5_4_MINI`. Cross-reference this plan file.

In section "6. Enabling work", mark 6.1 (configurable per role), 6.2 (cost attribution per role), and 6.3 (latency instrumentation) as done, each with a one-line pointer to this plan file and the commit(s) that landed it. Leave 6.4 and 6.5 as-is except: correct 6.4's claim that `clone_repo` has no auth (Workstream D1 landed 2026-07-23, PR #28) — the actual remaining gap is that `CORPUS_PAT_AVAILABLE=1` live verification against a real private fixture has never been run; reword 6.4 to say that, not "auth doesn't exist."

- [ ] **Step 5: Commit the doc update**

```bash
git add apps/backend/docs/model-selection.md
git commit -m "docs: mark model-selection Phase 0 (6.1-6.3) done, correct stale 6.4 claim"
```

---

## What Phase 0 does NOT include (explicitly out of scope for this plan)

- Actually picking a different model for any role based on evidence (that's Phase 1-3 of section 7, gated on a real corpus run — see `docs/model-selection.md` section 6.4's now-corrected state).
- Running `scripts/corpus_check.py --assert-live` against the private fixtures (needs `CORPUS_PAT_AVAILABLE=1` and a real PAT with read access to the `misi-e2e-validation-*` repos — a manual step for whoever runs Phase 0's baseline, not something this plan automates).
- Cross-provider support (deferred per the user's explicit choice — OpenAI-only this round).
- A UI/dashboard for the breakdown — Task 6 only exposes it via the existing status JSON; visualizing it (the "Pareto chart" section 7's Phase 0 deliverable calls for) is a follow-up once real data exists to chart.
