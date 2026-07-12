# Observability: Agent Calls, Per-Agent Timing, Per-Subgraph Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record which agents/tools ran in each analysis-conductor iteration, time each agent invocation, and break job cost down per subgraph.

**Architecture:** Analysis-subgraph agents accumulate `agent_calls` records in `AnalysisState` (same reducer pattern as `bundle_ids`) and flush them to the job's ANALYSIS artifact when the subgraph finishes. Per-subgraph cost is derived by diffing the existing job-wide `CostCallback` cumulative total at the three subgraph-completion points `job_runner` already observes.

**Tech Stack:** Python, LangGraph (`StateGraph`, `Send`, reducers), Pydantic, MongoDB (via existing `JobRepositoryPort`/`JobDAO`), pytest + pytest-asyncio, `uv`.

## Global Constraints

- Run all tests with `uv run pytest ...` — never call `pytest` or `python` directly.
- No new exception handling beyond what already exists — if `agent.run()` raises, the node fails exactly as it does today.
- No emojis in code, logs, or docs.
- Keep functions/modules short; don't add fields or parameters beyond what each task specifies.
- Full design context: `docs/superpowers/specs/2026-07-12-observability-tracking-design.md`.

---

### Task 1: `AgentCallRecord` model

**Files:**
- Modify: `src/models/results.py:54-56` (insert new class between `EvidenceBundle` and `AnalysisResult`)
- Test: `tests/unit/test_result_models.py`

**Interfaces:**
- Produces: `AgentCallRecord` (Pydantic `BaseModel`) with fields `conductor_iteration: int`, `agent_type: str`, `domain: str`, `tools_used: list[str]`, `react_iterations: int`, `started_at: str`, `finished_at: str`, `bundle_id: str`. Consumed by Task 2 (`domain_agent.py`).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_result_models.py` (add `AgentCallRecord` to the existing `from src.models.results import (...)` block at the top of the file):

```python
def test_agent_call_record_round_trip():
    record = AgentCallRecord(
        conductor_iteration=1,
        agent_type="vulnerability_agent",
        domain="vulnerability",
        tools_used=["npm_audit"],
        react_iterations=1,
        started_at="2026-07-12T00:00:00+00:00",
        finished_at="2026-07-12T00:00:05+00:00",
        bundle_id="bundle-1",
    )
    data = record.model_dump()
    record2 = AgentCallRecord(**data)
    assert record2.tools_used == ["npm_audit"]
    assert record2.conductor_iteration == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_result_models.py::test_agent_call_record_round_trip -v`
Expected: FAIL with `ImportError: cannot import name 'AgentCallRecord'`

- [ ] **Step 3: Write minimal implementation**

In `src/models/results.py`, insert immediately after the `EvidenceBundle` class (after its `verification_note` field, before `class AnalysisResult`):

```python
class AgentCallRecord(BaseModel):
    conductor_iteration: int
    agent_type: str
    domain: str
    tools_used: list[str]
    react_iterations: int
    started_at: str
    finished_at: str
    bundle_id: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_result_models.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/models/results.py tests/unit/test_result_models.py
git commit -m "feat: add AgentCallRecord model for agent-call telemetry"
```

---

### Task 2: Agents return tool usage + iteration count; `domain_agent` builds and accumulates `AgentCallRecord`

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/agents/base_agent.py:119-211` (`_react_loop`, `BaseAgent.run`)
- Modify: `src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py:34-63` (`VulnerabilityAgent.run`)
- Modify: `src/main_graph/subgraphs/analysis/nodes/domain_agent.py` (build `AgentCallRecord`, time the call)
- Modify: `src/main_graph/subgraphs/analysis/state.py` (new `agent_calls` field)
- Modify: `src/main_graph/subgraphs/analysis/graph.py:28-42` (`_after_conductor` — reset `agent_calls` per `Send`, mirroring `bundle_ids`)
- Test: `tests/unit/test_base_agent.py` (unpack the new tuple return in every existing test that calls `run()`/`_react_loop`, add assertions on `tools_used`/`react_iterations`)

**Interfaces:**
- Consumes: `AgentCallRecord` from Task 1.
- Produces: `BaseAgent.run(dispatch, prep) -> tuple[EvidenceBundle, list[str], int]` (bundle, tools_used, react_iterations) — the new contract every caller and subclass must follow. `domain_agent` returns `{"bundle_ids": [...], "agent_calls": [dict]}`. `AnalysisState.agent_calls: Annotated[list[dict], operator.add]` — consumed by Task 3.

- [ ] **Step 1: Update existing tests to unpack the new tuple return (still fails — implementation not changed yet)**

In `tests/unit/test_base_agent.py`, apply these exact replacements:

```python
# test_vulnerability_agent_run_extracts_all_audit_findings
    with patch.object(vulnerability_agent, "npm_audit", audit), \
         patch.object(vulnerability_agent.settings, "vuln_min_severity", "high"):
        bundle, tools_used, react_iterations = await vulnerability_agent.VulnerabilityAgent().run(_dispatch(), _prep())

    assert isinstance(bundle, EvidenceBundle)
    assert tools_used == ["npm_audit"]
    assert react_iterations == 1
    assert bundle.confidence == 1.0
```

```python
# test_agent_run_accepts_bare_async_functions
    with patch("src.main_graph.subgraphs.analysis.agents.base_agent._llm", mock_llm):
        bundle, tools_used, react_iterations = await _react_loop(_dispatch(), _prep(), [npm_audit], "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}")

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.domain == "vulnerabilities"
    assert tools_used == []
    assert react_iterations == 1
```

```python
# test_react_loop_self_corrects_then_passes
    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.85
    assert bundle.verification_note is None
    assert tools_used == ["verification_feedback"]
    assert react_iterations == 2
    assert critic.await_count == 2  # rejected once, re-verified after self-correction
    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 2
```

```python
# test_react_loop_attaches_note_when_budget_exhausted
    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "_MAX_ITERATIONS", 2), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.findings == [finding]  # kept, not pruned
    assert bundle.confidence == 0.1
    assert bundle.verification_note == "express finding unsupported"
    assert tools_used == ["verification_feedback"]
    assert react_iterations == 2
```

```python
# test_react_loop_critic_failure_degrades_to_pass
    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.9
    assert bundle.verification_note is None
    assert tools_used == []
    assert react_iterations == 1
```

```python
# test_react_loop_survives_malformed_decision_then_recovers
    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.findings == [finding]
    assert bundle.confidence == 0.9
    assert tools_used == []
    assert react_iterations == 2
    assert mock_llm.with_structured_output.return_value.ainvoke.await_count == 2
```

```python
# test_react_loop_skips_critic_when_no_findings
    with patch.object(base_agent, "_llm", mock_llm), \
         patch.object(base_agent, "critique_findings", critic):
        bundle, tools_used, react_iterations = await base_agent._react_loop(
            _dispatch(), _prep(), [],
            "system {domain} {hypothesis} {packages} {context} {tool_descriptions} {max_iter}",
        )

    assert bundle.confidence == 0.4
    assert tools_used == []
    assert react_iterations == 1
    critic.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_base_agent.py -v`
Expected: FAIL — `ValueError: not enough values to unpack (expected 3, got 1)` (or similar) on every test, since `run()`/`_react_loop` still return a bare `EvidenceBundle`.

- [ ] **Step 3: Change `_react_loop` and `BaseAgent.run` to return `(bundle, tools_used, react_iterations)`**

In `src/main_graph/subgraphs/analysis/agents/base_agent.py`, inside the `for iteration in range(_MAX_ITERATIONS):` loop in `_react_loop`, add a tracking line as the very first statement of the loop body:

```python
    for iteration in range(_MAX_ITERATIONS):
        react_iterations = iteration + 1
        prompt = (
```

Then change the function's final return statement from:

```python
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

to:

```python
    bundle = EvidenceBundle(
        domain=dispatch.domain,
        hypothesis=dispatch.hypothesis,
        packages_to_focus=dispatch.packages_to_focus,
        findings=decision.findings if decision else [],
        summary=decision.summary if decision else "No results.",
        confidence=confidence,
        verification_note=note,
    )
    return bundle, [tr.tool for tr in tool_results], react_iterations
```

Also update the function's return type annotation:

```python
async def _react_loop(
    dispatch: AgentDispatch,
    prep: PrepResult,
    tools: list,
    system_prompt: str,
) -> tuple[EvidenceBundle, list[str], int]:
```

And `BaseAgent.run`'s signature:

```python
    async def run(self, dispatch: AgentDispatch, prep: PrepResult) -> tuple[EvidenceBundle, list[str], int]:
        return await _react_loop(dispatch, prep, self.get_tools(prep), self.system_prompt)
```

- [ ] **Step 4: Change `VulnerabilityAgent.run` to return the same tuple**

In `src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py`, change the signature and both return statements:

```python
    async def run(self, dispatch: AgentDispatch, prep: PrepResult) -> tuple[EvidenceBundle, list[str], int]:
        output = await npm_audit(
            repo_path=prep.repo_path,
            detected_package_manager=prep.detected_package_manager,
        )
        min_severity = settings.vuln_min_severity
        error = output.get("error") if isinstance(output, dict) else None
        findings = [] if error else parse_audit_findings(output, min_severity)

        if error:
            logger.warning("vulnerability_agent: audit failed: %s", error)
            summary = f"Dependency audit failed: {error}"
        else:
            logger.info(
                "vulnerability_agent: audited whole tree, %d finding(s) at severity>=%s",
                len(findings), min_severity,
            )
            summary = (
                f"Audited the full dependency tree via {prep.detected_package_manager} audit. "
                f"{len(findings)} finding(s) at severity >= {min_severity}."
            )

        bundle = EvidenceBundle(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages_to_focus=[],
            findings=findings,
            summary=summary,
            confidence=0.3 if error else 1.0,
        )
        return bundle, ["npm_audit"], 1
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_base_agent.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Update `domain_agent.py` to build and return an `AgentCallRecord`**

Replace the full contents of `src/main_graph/subgraphs/analysis/nodes/domain_agent.py`:

```python
from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.agents.web_research_agent import WebResearchAgent
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AgentCallRecord, AgentDispatch

logger = logging.getLogger(__name__)


async def domain_agent(state: AnalysisState, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])
    dispatch = AgentDispatch(**state["current_dispatch"])

    agent_class = REGISTRY.get(dispatch.agent_type, WebResearchAgent)
    agent = agent_class()

    logger.info("domain_agent: type=%s domain=%s hypothesis=%s",
                dispatch.agent_type, dispatch.domain, dispatch.hypothesis[:60])

    started_at = datetime.now(UTC).isoformat()
    bundle, tools_used, react_iterations = await agent.run(dispatch, prep)
    finished_at = datetime.now(UTC).isoformat()

    bundle_id = await dao.save_bundle(bundle)

    record = AgentCallRecord(
        conductor_iteration=state["conductor_iteration"],
        agent_type=dispatch.agent_type,
        domain=dispatch.domain,
        tools_used=tools_used,
        react_iterations=react_iterations,
        started_at=started_at,
        finished_at=finished_at,
        bundle_id=bundle_id,
    )

    logger.info("domain_agent: saved bundle_id=%s findings=%d", bundle_id, len(bundle.findings))
    return {"bundle_ids": [bundle_id], "agent_calls": [record.model_dump()]}
```

- [ ] **Step 7: Add `agent_calls` to `AnalysisState`**

In `src/main_graph/subgraphs/analysis/state.py`, add the field next to `bundle_ids`:

```python
class AnalysisState(TypedDict):
    # From MainState (matched by key name)
    job_id: str
    concern: str
    prep_result_id: str

    # Internal
    conductor_decision: NotRequired[AnalysisConductorDecision]
    current_dispatch: NotRequired[dict]   # AgentDispatch.model_dump() for domain_agent nodes
    bundle_ids: Annotated[list[str], operator.add]
    agent_calls: Annotated[list[dict], operator.add]   # AgentCallRecord.model_dump() per domain_agent call
    conductor_iteration: NotRequired[int]

    # Output (written back to MainState)
    analysis_result_id: NotRequired[str]
```

- [ ] **Step 8: Reset `agent_calls` per `Send`, mirroring `bundle_ids`**

In `src/main_graph/subgraphs/analysis/graph.py`, in `_after_conductor`:

```python
        sends.append(
            Send("domain_agent", {
                **state,
                "current_dispatch": dispatch_dict,
                "bundle_ids": [],
                "agent_calls": [],
            })
        )
```

- [ ] **Step 9: Run the full analysis unit test suite**

Run: `uv run pytest tests/unit/test_base_agent.py tests/unit/test_result_models.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/main_graph/subgraphs/analysis/agents/base_agent.py \
        src/main_graph/subgraphs/analysis/agents/vulnerability_agent.py \
        src/main_graph/subgraphs/analysis/nodes/domain_agent.py \
        src/main_graph/subgraphs/analysis/state.py \
        src/main_graph/subgraphs/analysis/graph.py \
        tests/unit/test_base_agent.py
git commit -m "feat: agents report tool usage and iteration count; domain_agent logs AgentCallRecord"
```

---

### Task 3: Persist `agent_calls` to the ANALYSIS artifact on subgraph exit

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`
- Modify: `tests/subgraphs/conftest.py` (`job_repo` fixture: `MagicMock()` → `AsyncMock()`)
- Test: `tests/subgraphs/test_analysis_subgraph.py`

**Interfaces:**
- Consumes: `state["agent_calls"]` (list of dicts, from Task 2), `get_services(config)["job_repo"]` (existing `JobRepositoryPort`, already carries `update_artifact_data`).
- Produces: ANALYSIS artifact document gains `agent_calls: list[dict]`.

- [ ] **Step 1: Fix the subgraph test fixture — `job_repo` must be awaitable**

In `tests/subgraphs/conftest.py`, change:

```python
    return {
        "configurable": {
            "result_dao": result_dao,
            "container": container_mock,
            "docker_tool": MagicMock(),
            "job_repo": MagicMock(),
        }
    }
```

to:

```python
    return {
        "configurable": {
            "result_dao": result_dao,
            "container": container_mock,
            "docker_tool": MagicMock(),
            "job_repo": AsyncMock(),
        }
    }
```

(`AsyncMock` is already imported at the top of this file.)

- [ ] **Step 2: Write the failing test — assert `agent_calls` reaches the artifact**

In `tests/subgraphs/test_analysis_subgraph.py`, at the end of `test_analysis_dispatches_agent_and_saves_result` (after the existing `assert len(analysis.evidence_bundle_ids) == 1` line), add:

```python
    job_repo = subgraph_config["configurable"]["job_repo"]
    job_repo.update_artifact_data.assert_awaited_once()
    call = job_repo.update_artifact_data.await_args
    assert call.args[0] == job_id
    assert call.args[1] == "analysis"
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 1
    assert agent_calls[0]["agent_type"] == "vulnerability_agent"
    assert agent_calls[0]["domain"] == "vulnerability"
    assert agent_calls[0]["tools_used"] == ["npm_audit"]
    assert agent_calls[0]["react_iterations"] == 1
    assert agent_calls[0]["conductor_iteration"] == 1
    assert agent_calls[0]["bundle_id"] == analysis.evidence_bundle_ids[0]
    assert agent_calls[0]["started_at"]
    assert agent_calls[0]["finished_at"]
```

Also, at the end of `test_analysis_accumulates_bundles_from_parallel_agents` (after `assert len(analysis.findings) == 2`), add a looser structural check — this test's second agent (`maintenance_agent`) goes through the real (unmocked) `critique_findings`, so its exact `react_iterations`/`tools_used` are not deterministic in this test and must not be asserted:

```python
    job_repo = subgraph_config["configurable"]["job_repo"]
    job_repo.update_artifact_data.assert_awaited_once()
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    assert len(agent_calls) == 2
    assert {c["agent_type"] for c in agent_calls} == {"vulnerability_agent", "maintenance_agent"}
    for c in agent_calls:
        assert c["started_at"]
        assert c["finished_at"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/subgraphs/test_analysis_subgraph.py -v`
Expected: FAIL — `job_repo.update_artifact_data` was never awaited (0 calls), since `save_analysis_result` doesn't call it yet.
(Requires Docker running: `colima start` if needed.)

- [ ] **Step 4: Implement the flush in `save_analysis_result.py`**

Replace the full contents of `src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py`:

```python
from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.constants import ANALYSIS
from src.main_graph.subgraphs.analysis.state import AnalysisState
from src.models.results import AnalysisResult

logger = logging.getLogger(__name__)


async def save_analysis_result(state: AnalysisState, config: RunnableConfig) -> dict:
    services = get_services(config)
    dao = services["result_dao"]
    job_repo = services["job_repo"]

    bundle_ids = state.get("bundle_ids") or []
    bundles = await dao.get_bundles(bundle_ids)

    all_findings = [f for b in bundles for f in b.findings]

    result = AnalysisResult(
        job_id=state["job_id"],
        concern=state["concern"],
        findings=all_findings,
        evidence_bundle_ids=bundle_ids,
        iteration_count=state.get("conductor_iteration") or 0,
    )
    analysis_result_id = await dao.save_analysis(result)

    await job_repo.update_artifact_data(
        state["job_id"], ANALYSIS, {"agent_calls": state.get("agent_calls") or []}
    )

    logger.info(
        "save_analysis_result: saved analysis_result_id=%s findings=%d",
        analysis_result_id, len(all_findings),
    )
    return {"analysis_result_id": analysis_result_id}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/subgraphs/test_analysis_subgraph.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/analysis/nodes/save_analysis_result.py \
        tests/subgraphs/conftest.py \
        tests/subgraphs/test_analysis_subgraph.py
git commit -m "feat: flush agent_calls to the ANALYSIS artifact on subgraph exit"
```

---

### Task 4: Per-subgraph cost via diffing at existing subgraph boundaries

**Files:**
- Modify: `src/services/job_runner.py` (`_stream_graph`, `run_analysis`, `resume_analysis`)
- Test: `tests/unit/services/test_job_runner.py`

**Interfaces:**
- Consumes: existing `CostCallback.cost() -> float` (unchanged), existing `dao.update_artifact_data(job_id, node, data)` (unchanged).
- Produces: PREP/ANALYSIS/REPORT artifacts each gain a `cost: float` field representing that subgraph's share of total job cost.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/services/test_job_runner.py` (add `MagicMock` to the existing `from unittest.mock import AsyncMock, patch` import line):

```python
@pytest.mark.asyncio
async def test_run_analysis_records_cost_per_subgraph():
    dao = _make_dao()

    async def fake_stream(*args, **kwargs):
        yield {"prep": {}}
        yield {"analysis": {"analysis_result_id": "ares-1"}}
        yield {"report": {"report_result_id": None}}

    fake_cost_cb = MagicMock()
    fake_cost_cb.cost = MagicMock(side_effect=[0.01, 0.03, 0.07, 0.07])

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.CostCallback", return_value=fake_cost_cb),
    ):
        mock_graph.astream = fake_stream
        await run_analysis("job-2", "https://github.com/x/y", "security", autopilot=False, dao=dao)

    dao.update_artifact_data.assert_any_call("job-2", "prep", {"cost": 0.01})
    dao.update_artifact_data.assert_any_call("job-2", "analysis", {"cost": 0.02})
    dao.update_artifact_data.assert_any_call("job-2", "report", {"cost": 0.04})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/test_job_runner.py::test_run_analysis_records_cost_per_subgraph -v`
Expected: FAIL — `update_artifact_data` was not called with `{"cost": ...}` (assertion error), since `_stream_graph` doesn't diff cost yet.

- [ ] **Step 3: Implement cost diffing in `_stream_graph`**

In `src/services/job_runner.py`, change the `_stream_graph` signature and body:

```python
async def _stream_graph(
    graph, input_data, config, dao: JobRepositoryPort, job_id: str, cost_cb: CostCallback,
) -> None:
    prev_cost = 0.0
    async for chunk in graph.astream(input_data, config, stream_mode="updates"):
        for node_name, node_update in chunk.items():
            logger.info("job=%s node=%s completed", job_id, node_name)

            if node_name in (PREP, ANALYSIS, REPORT):
                cost_now = cost_cb.cost()
                await dao.update_artifact_data(
                    job_id, node_name, {"cost": round(cost_now - prev_cost, 6)}
                )
                prev_cost = cost_now

            if node_name == PREP:
                status = "failed" if node_update.get("discovery_error") else "done"
                await dao.complete_artifact(job_id, PREP, status)
                if status == "done":
                    await dao.start_artifact(job_id, ANALYSIS)

            elif node_name == ANALYSIS:
                await dao.complete_artifact(job_id, ANALYSIS, "done")
                if node_update.get("analysis_result_id"):
                    await dao.start_artifact(job_id, REPORT)

            elif node_name == REPORT:
                report_result_id = node_update.get("report_result_id")
                await dao.complete_artifact(job_id, REPORT, "done")
                if report_result_id:
                    result_dao = get_result_dao()
                    report = await result_dao.get_report(report_result_id)
                    await dao.update_artifact_data(job_id, REPORT, {"output": report.model_dump()})
```

Then update both call sites to pass `cost_cb` through. In `run_analysis`:

```python
    try:
        await _stream_graph(
            main_graph,
            {"repo_url": repo_url, "concern": concern, "job_id": job_id,
             "autopilot": autopilot, "messages": []},
            config, dao, job_id, cost_cb,
        )
```

And in `resume_analysis`:

```python
    try:
        await _stream_graph(
            main_graph,
            Command(resume=user_message),
            config,
            dao,
            job_id,
            cost_cb,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/services/test_job_runner.py -v`
Expected: PASS (all tests, including the pre-existing `test_run_analysis_marks_failed_on_exception`)

- [ ] **Step 5: Commit**

```bash
git add src/services/job_runner.py tests/unit/services/test_job_runner.py
git commit -m "feat: record per-subgraph cost by diffing cumulative cost at subgraph boundaries"
```

---

## Self-Review Notes

- **Spec coverage:** Part 1 (agent-call log + per-agent timing) → Tasks 1-3. Part 2 (per-subgraph cost) → Task 4. Non-goals (discovery/report agent logs, frontend surface) intentionally have no task.
- **Type consistency verified:** `AgentCallRecord` fields (Task 1) match exactly what `domain_agent.py` constructs (Task 2) and what the artifact assertions read (Task 3). `run()`'s tuple order `(bundle, tools_used, react_iterations)` is identical across `base_agent.py`, `vulnerability_agent.py`, and every call site.
- **Sequencing note:** Tasks 2 and 3 must land in order — Task 2 alone leaves `save_analysis_result.py` unaware of `agent_calls` (harmless, just not yet persisted); Task 3 depends on `state["agent_calls"]` existing, which Task 2 introduces.
