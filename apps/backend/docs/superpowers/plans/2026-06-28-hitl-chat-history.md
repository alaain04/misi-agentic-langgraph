# HITL Node Chat History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken `push_proposal`/`update_proposal` DAO mechanism with a generic `push_artifact_message` that stores an ordered chat thread + structured data on each HITL artifact, and extend `finding_reviewer` to persist its interrupt data the same way.

**Architecture:** Two HITL nodes (`investigation_planner`, `finding_reviewer`) each gain a `messages: []` chat thread and a `data: {}` structured payload on their artifact document in MongoDB. A new DAO method `push_artifact_message` handles both appending to an existing artifact and upserting a new one (needed because `finding_reviewer` calls it before the runner creates its artifact). The API response schema is unchanged — the client derives the awaiting node from `status == "awaiting_approval"` plus whichever artifact has `status == "running"`.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, Motor (async MongoDB), pytest with `asyncio_mode = "auto"`, uv

## Global Constraints

- Run tests with `uv run pytest <path> -v`, never `python -m pytest`
- Run the server with `uv run uvicorn src.main:app`
- Never use `pip install` — use `uv add`
- `asyncio_mode = "auto"` in pyproject.toml — no `@pytest.mark.asyncio` needed
- All work is in `apps/backend/` (cwd for all commands below)

---

### Task 1: Add `push_artifact_message` to DAO port and implementation

Replace the broken `push_proposal` / `update_proposal` pair with a single `push_artifact_message` method that is generic across HITL nodes. The old methods are removed entirely.

**Files:**
- Modify: `src/domain/ports/job_repository_port.py`
- Modify: `src/services/job_dao.py`

**Interfaces:**
- Produces: `push_artifact_message(job_id: str, node: str, message: dict) -> None` — available on `JobRepositoryPort` and `JobDAO`

---

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/test_job_dao_push_artifact_message.py`:

```python
"""Unit test: push_artifact_message contract via the abstract port."""
from src.domain.ports.job_repository_port import JobRepositoryPort
import inspect


def test_push_artifact_message_is_on_port():
    members = {name for name, _ in inspect.getmembers(JobRepositoryPort)}
    assert "push_artifact_message" in members


def test_push_proposal_removed_from_port():
    members = {name for name, _ in inspect.getmembers(JobRepositoryPort)}
    assert "push_proposal" not in members
    assert "update_proposal" not in members
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/services/test_job_dao_push_artifact_message.py -v
```

Expected: FAIL — `push_artifact_message` not found, `push_proposal` still present.

- [ ] **Step 3: Update `src/domain/ports/job_repository_port.py`**

Remove `push_proposal` and `update_proposal`. Add `push_artifact_message`:

```python
from abc import ABC, abstractmethod

from src.models.job import Job, JobStatus


class JobRepositoryPort(ABC):
    @abstractmethod
    async def create(self, job: Job) -> Job: ...

    @abstractmethod
    async def get(self, job_id: str) -> Job | None: ...

    @abstractmethod
    async def update_status(self, job_id: str, status: JobStatus) -> None: ...

    @abstractmethod
    async def save_result(self, job_id: str, result: dict) -> None: ...

    @abstractmethod
    async def mark_failed(self, job_id: str) -> None: ...

    @abstractmethod
    async def mark_cancelled(self, job_id: str) -> None: ...

    @abstractmethod
    async def start_artifact(self, job_id: str, node: str) -> None: ...

    @abstractmethod
    async def complete_artifact(self, job_id: str, node: str, status: str) -> None: ...

    @abstractmethod
    async def push_artifact_message(self, job_id: str, node: str, message: dict) -> None: ...

    @abstractmethod
    async def update_artifact_data(
        self, job_id: str, node: str, data: dict
    ) -> None: ...

    @abstractmethod
    async def get_pending(self) -> list[Job]: ...

    @abstractmethod
    async def list(
        self,
        page: int = 1,
        limit: int = 10,
        status: JobStatus | None = None,
        trace_id: str | None = None,
    ) -> tuple[list[Job], int]: ...
```

- [ ] **Step 4: Implement `push_artifact_message` in `src/services/job_dao.py`**

Remove `push_proposal` and `update_proposal` methods. Add `push_artifact_message` after `complete_artifact`:

```python
async def push_artifact_message(self, job_id: str, node: str, message: dict) -> None:
    """Append a chat message to the artifact's messages array.

    Upserts the artifact with status 'running' if it does not exist yet.
    This handles finding_reviewer, which calls this before the runner creates its artifact.
    """
    result = await self._col.update_one(
        {"_id": job_id, "artifacts.node": node},
        {"$push": {"artifacts.$.messages": message}},
    )
    if result.matched_count == 0:
        now = datetime.now(UTC)
        await self._col.update_one(
            {"_id": job_id},
            {"$push": {"artifacts": {
                "node": node,
                "status": "running",
                "started_at": now,
                "completed_at": None,
                "messages": [message],
            }}},
        )
```

The full updated `src/services/job_dao.py` (replace entire file):

```python
import logging
from datetime import UTC, datetime

from src.db.connection import get_db
from src.domain.ports.job_repository_port import JobRepositoryPort
from src.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


class JobDAO(JobRepositoryPort):
    def __init__(self):
        self._col = get_db()["jobs"]

    async def create(self, job: Job) -> Job:
        await self._col.insert_one(job.to_doc())
        return job

    async def get(self, job_id: str) -> Job | None:
        doc = await self._col.find_one({"_id": job_id})
        if doc is None:
            return None
        doc["id"] = doc.pop("_id")
        return Job(**doc)

    async def update_status(self, job_id: str, status: JobStatus) -> None:
        await self._col.update_one({"_id": job_id}, {"$set": {"status": status}})

    async def save_result(self, job_id: str, result: dict) -> None:
        await self._col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.done,
                    "result": result,
                    "completed_at": datetime.now(UTC),
                }
            },
        )

    async def mark_failed(self, job_id: str) -> None:
        await self._col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.failed,
                    "completed_at": datetime.now(UTC),
                }
            },
        )

    async def mark_cancelled(self, job_id: str) -> None:
        await self._col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.cancelled,
                    "completed_at": datetime.now(UTC),
                }
            },
        )

    async def start_artifact(self, job_id: str, node: str) -> None:
        """Insert or update an artifact entry as 'running'."""
        now = datetime.now(UTC)
        artifact = {
            "node": node,
            "status": "running",
            "started_at": now,
            "completed_at": None,
        }
        result = await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {
                "$set": {
                    "artifacts.$.status": "running",
                    "artifacts.$.started_at": now,
                    "artifacts.$.completed_at": None,
                }
            },
        )
        if result.matched_count == 0:
            await self._col.update_one(
                {"_id": job_id},
                {"$push": {"artifacts": artifact}},
            )

    async def complete_artifact(self, job_id: str, node: str, status: str) -> None:
        """Mark an artifact as done or failed. Creates entry if missing."""
        now = datetime.now(UTC)
        result = await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$set": {"artifacts.$.status": status, "artifacts.$.completed_at": now}},
        )
        if result.matched_count == 0:
            await self._col.update_one(
                {"_id": job_id},
                {
                    "$push": {
                        "artifacts": {
                            "node": node,
                            "status": status,
                            "started_at": now,
                            "completed_at": now,
                        }
                    }
                },
            )

    async def push_artifact_message(self, job_id: str, node: str, message: dict) -> None:
        """Append a chat message to the artifact's messages array.

        Upserts the artifact with status 'running' if it does not exist yet.
        This handles finding_reviewer, which calls this before the runner creates its artifact.
        """
        result = await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$push": {"artifacts.$.messages": message}},
        )
        if result.matched_count == 0:
            now = datetime.now(UTC)
            await self._col.update_one(
                {"_id": job_id},
                {"$push": {"artifacts": {
                    "node": node,
                    "status": "running",
                    "started_at": now,
                    "completed_at": None,
                    "messages": [message],
                }}},
            )

    async def update_artifact_data(self, job_id: str, node: str, data: dict) -> None:
        """Merge extra fields into an existing artifact entry."""
        update_fields = {f"artifacts.$.{k}": v for k, v in data.items()}
        await self._col.update_one(
            {"_id": job_id, "artifacts.node": node},
            {"$set": update_fields},
        )

    async def get_pending(self) -> list[Job]:
        cursor = self._col.find({"status": JobStatus.pending})
        jobs = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            jobs.append(Job(**doc))
        return jobs

    async def list(
        self,
        page: int = 1,
        limit: int = 10,
        status: JobStatus | None = None,
        trace_id: str | None = None,
    ) -> tuple[list[Job], int]:
        query: dict = {}
        if status is not None:
            query["status"] = status
        if trace_id is not None:
            query["_id"] = {"$regex": trace_id, "$options": "i"}
        total = await self._col.count_documents(query)
        skip = (page - 1) * limit
        cursor = self._col.find(query).sort("created_at", -1).skip(skip).limit(limit)
        jobs = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            jobs.append(Job(**doc))
        return jobs, total
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/services/test_job_dao_push_artifact_message.py -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
uv run pytest tests/unit/ -v
```

Expected: All passing. If any test mocks `push_proposal` or `update_proposal`, it will fail here — fix those mocks in the same commit.

- [ ] **Step 7: Commit**

```bash
git add src/domain/ports/job_repository_port.py src/services/job_dao.py tests/unit/services/test_job_dao_push_artifact_message.py
git commit -m "feat: replace push_proposal/update_proposal with push_artifact_message in DAO"
```

---

### Task 2: Update `investigation_planner_service.py` to use the new message API

Replace the two broken DAO calls (`push_proposal`, `update_proposal`) with `push_artifact_message` + `update_artifact_data`. Add tests that verify the service pushes the correct messages on approve and on the change-then-approve loop.

**Files:**
- Modify: `src/main_graph/nodes/investigation_planner_service.py`
- Modify: `tests/unit/nodes/test_investigation_planner.py`

**Interfaces:**
- Consumes: `push_artifact_message(job_id, node, message)` and `update_artifact_data(job_id, node, data)` from Task 1
- Consumes: `INVESTIGATION_PLANNER = "investigation_planner"` from `src/main_graph/constants.py`

---

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/nodes/test_investigation_planner.py`:

```python
import json
from unittest.mock import AsyncMock, patch

from src.main_graph.constants import INVESTIGATION_PLANNER
from src.main_graph.nodes.investigation_planner_service import investigation_planner_service


_PLAN_LLM_RESPONSE = {
    "hypotheses": [{
        "id": "h1",
        "dep_name": "lodash",
        "statement": "lodash may expose prototype pollution",
        "risk_theme": "vulnerability",
        "rationale": "known CVEs",
        "skills": ["VulnerabilitySkill"],
    }],
    "rationale": "security focus",
    "dep_filter": None,
}

_PLANNER_STATE = {
    "job_id": "job-1",
    "concern": "security audit",
    "discovery_summary": "React app with 50 deps",
    "sbom_cyclonedx": {"components": [{"name": "lodash"}]},
    "messages": [],
}


async def test_planner_service_pushes_assistant_and_human_messages_on_approve():
    dao = AsyncMock()

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = [
        AsyncMock(content=json.dumps(_PLAN_LLM_RESPONSE)),  # _run_planner
        AsyncMock(content="approve"),                        # _classify_intent
    ]

    with (
        patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm),
        patch("src.main_graph.nodes.investigation_planner_service.interrupt", return_value="looks good"),
    ):
        result = await investigation_planner_service(_PLANNER_STATE, dao)

    assert "investigation_plan" in result

    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 2

    assert calls[0].args[0] == "job-1"
    assert calls[0].args[1] == INVESTIGATION_PLANNER
    assert calls[0].args[2]["role"] == "assistant"
    assert "content" in calls[0].args[2]
    assert "created_at" in calls[0].args[2]

    assert calls[1].args[1] == INVESTIGATION_PLANNER
    assert calls[1].args[2]["role"] == "human"
    assert calls[1].args[2]["content"] == "looks good"
    assert calls[1].args[2]["action"] == "approve"

    dao.update_artifact_data.assert_awaited_once()
    data_call = dao.update_artifact_data.await_args_list[0]
    assert data_call.args[1] == INVESTIGATION_PLANNER
    assert "plan" in data_call.args[2]["data"]


async def test_planner_service_pushes_four_messages_on_change_then_approve():
    dao = AsyncMock()

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = [
        AsyncMock(content=json.dumps(_PLAN_LLM_RESPONSE)),  # _run_planner (initial)
        AsyncMock(content="change"),                         # _classify_intent (first response)
        AsyncMock(content=json.dumps(_PLAN_LLM_RESPONSE)),  # _run_planner (re-plan)
        AsyncMock(content="approve"),                        # _classify_intent (second response)
    ]

    with (
        patch("src.main_graph.nodes.investigation_planner_service._llm", mock_llm),
        patch(
            "src.main_graph.nodes.investigation_planner_service.interrupt",
            side_effect=["focus on licenses", "ok proceed"],
        ),
    ):
        await investigation_planner_service(_PLANNER_STATE, dao)

    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 4
    assert calls[0].args[2]["role"] == "assistant"
    assert calls[1].args[2]["role"] == "human"
    assert calls[1].args[2]["action"] == "change"
    assert calls[1].args[2]["content"] == "focus on licenses"
    assert calls[2].args[2]["role"] == "assistant"
    assert calls[3].args[2]["role"] == "human"
    assert calls[3].args[2]["action"] == "approve"

    assert dao.update_artifact_data.await_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/nodes/test_investigation_planner.py::test_planner_service_pushes_assistant_and_human_messages_on_approve tests/unit/nodes/test_investigation_planner.py::test_planner_service_pushes_four_messages_on_change_then_approve -v
```

Expected: FAIL — `push_artifact_message` not called, `push_proposal` called instead (which raises `AttributeError` on the mock or does nothing).

- [ ] **Step 3: Update `src/main_graph/nodes/investigation_planner_service.py`**

Add the import for `INVESTIGATION_PLANNER` near the top (after the existing imports):

```python
from src.main_graph.constants import INVESTIGATION_PLANNER
```

Replace the `while True` loop body. The full updated function (only `investigation_planner_service` changes; all helpers remain identical):

```python
async def investigation_planner_service(
    state: MainState,
    dao: JobRepositoryPort,
    vector_store=None,
) -> dict | Command:
    """HITL loop: present plan, classify intent, loop on change, exit on approve/cancel."""
    job_id = state["job_id"]
    plan = await _run_planner(state)

    while True:
        assistant_msg = _present_plan(plan)
        created_at = datetime.now(UTC).isoformat()

        await dao.push_artifact_message(job_id, INVESTIGATION_PLANNER, {
            "role": "assistant",
            "content": assistant_msg,
            "created_at": created_at,
        })
        await dao.update_artifact_data(job_id, INVESTIGATION_PLANNER, {
            "data": {
                "plan": {
                    "hypotheses": [h.__dict__ for h in plan.hypotheses],
                    "rationale": plan.rationale,
                }
            }
        })

        user_input: str = interrupt({
            "investigation_plan": plan.__dict__,
            "assistant_message": assistant_msg,
        })

        if vector_store:
            try:
                await vector_store.add_texts([f"Assistant: {assistant_msg}", f"User: {user_input}"])
            except Exception:
                logger.warning("investigation_planner: vector store add failed")

        intent = await _classify_intent(plan, user_input)
        await dao.push_artifact_message(job_id, INVESTIGATION_PLANNER, {
            "role": "human",
            "content": user_input,
            "created_at": datetime.now(UTC).isoformat(),
            "action": intent,
        })

        new_messages = [AIMessage(content=assistant_msg), HumanMessage(content=user_input)]

        if intent == "approve":
            new_messages.append(AIMessage(content="Plan approved! Investigation is starting now."))
            return {"investigation_plan": plan, "messages": new_messages}

        if intent == "cancel":
            return Command(goto=END, update={"cancelled": True, "messages": new_messages})

        plan = await _run_planner(state, extra_instructions=user_input)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/nodes/test_investigation_planner.py -v
```

Expected: all passing, including the two new tests.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/investigation_planner_service.py tests/unit/nodes/test_investigation_planner.py
git commit -m "feat: store HITL chat history on investigation_planner artifact"
```

---

### Task 3: Update `finding_reviewer.py` to inject dao and store messages

The node currently takes only `state`. Update it to also accept `RunnableConfig`, inject the DAO from config, and store an assistant message + risk findings data before `interrupt()`, plus a human message after resume. Tests verify the DAO calls are correct for both the high-severity path (interrupt fires) and the auto-approve path (no interrupt).

**Files:**
- Modify: `src/main_graph/nodes/finding_reviewer.py`
- Modify: `tests/unit/nodes/test_finding_reviewer.py`

**Interfaces:**
- Consumes: `push_artifact_message(job_id, node, message)` and `update_artifact_data(job_id, node, data)` from Task 1
- Consumes: `get_services(config)` from `src/main_graph/config.py` — returns `{"job_repo": dao, ...}`
- Consumes: `FINDING_REVIEWER = "finding_reviewer"` from `src/main_graph/constants.py`

---

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/nodes/test_finding_reviewer.py`:

```python
from unittest.mock import AsyncMock, patch

from src.main_graph.constants import FINDING_REVIEWER
from src.main_graph.nodes.finding_reviewer import finding_reviewer


async def test_finding_reviewer_stores_messages_for_high_sev_findings():
    dao = AsyncMock()
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [_make_finding("lodash", 8.0, 0.8, "high", evidence_count=3)],
        "evidence": [],
        "review_iterations": 0,
    }

    with patch("src.main_graph.nodes.finding_reviewer.interrupt", return_value="acknowledged"):
        result = await finding_reviewer(state, config)

    assert result["review_approved"] is True

    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 2

    assert calls[0].args[0] == "job-1"
    assert calls[0].args[1] == FINDING_REVIEWER
    assert calls[0].args[2]["role"] == "assistant"
    assert "content" in calls[0].args[2]

    assert calls[1].args[1] == FINDING_REVIEWER
    assert calls[1].args[2]["role"] == "human"
    assert calls[1].args[2]["content"] == "acknowledged"
    assert calls[1].args[2]["action"] == "approve"

    dao.update_artifact_data.assert_awaited_once()
    data_call = dao.update_artifact_data.await_args_list[0]
    assert data_call.args[1] == FINDING_REVIEWER
    assert "risk_findings" in data_call.args[2]["data"]


async def test_finding_reviewer_no_messages_when_no_high_sev_findings():
    dao = AsyncMock()
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [_make_finding("lodash", 4.0, 0.9, "low", evidence_count=2)],
        "evidence": [],
        "review_iterations": 0,
    }

    result = await finding_reviewer(state, config)

    assert result["review_approved"] is True
    dao.push_artifact_message.assert_not_awaited()
    dao.update_artifact_data.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/nodes/test_finding_reviewer.py::test_finding_reviewer_stores_messages_for_high_sev_findings tests/unit/nodes/test_finding_reviewer.py::test_finding_reviewer_no_messages_when_no_high_sev_findings -v
```

Expected: FAIL — `finding_reviewer` does not accept `config`, `TypeError` on call.

- [ ] **Step 3: Replace `src/main_graph/nodes/finding_reviewer.py`**

```python
from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from src.main_graph.config import get_services
from src.main_graph.constants import FINDING_REVIEWER
from src.main_graph.state import MainState
from src.models.evidence import Evidence
from src.models.risk_finding import RiskFinding

logger = logging.getLogger(__name__)

_MAX_REVIEW_ITERATIONS = 2


async def _check_criteria(findings: list[RiskFinding], evidence: list[Evidence]) -> dict:
    failed: list[str] = []

    for f in findings:
        if f.severity in ("critical", "high"):
            if len(f.supporting_evidence) < 2:
                failed.append(f"{f.dep_name}: high-severity finding has fewer than 2 supporting evidence items")
            if f.risk_score > 7 and f.confidence < 0.5:
                failed.append(f"{f.dep_name}: risk_score={f.risk_score} but confidence={f.confidence:.2f} — insufficient evidence")
            if not f.alternatives and not f.recommendation:
                failed.append(f"{f.dep_name}: high-risk dependency has no alternative recommendation")

    for f in findings:
        if f.contradictions and not any(
            c.description[:20] in f.summary for c in f.contradictions
        ):
            failed.append(f"{f.dep_name}: contradictions not addressed in summary")

    return {
        "approved": len(failed) == 0,
        "failed_criteria": failed,
        "feedback": "; ".join(failed) if failed else "",
    }


def _format_findings_for_review(findings: list[RiskFinding]) -> str:
    lines = ["**High-Severity Findings Require Your Review:**\n"]
    for f in findings:
        lines.append(f"**{f.dep_name}** — {f.severity.upper()} (score: {f.risk_score}/10, confidence: {f.confidence:.0%})")
        lines.append(f"  {f.summary}")
        if f.recommendation:
            lines.append(f"  Recommendation: {f.recommendation}")
        if f.alternatives:
            lines.append(f"  Alternatives: {', '.join(f.alternatives)}")
        lines.append("")
    lines.append("Please review these findings. Respond to acknowledge or provide additional context.")
    return "\n".join(lines)


async def finding_reviewer(state: MainState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["job_repo"]
    job_id = state["job_id"]

    findings = state.get("risk_findings") or []
    evidence = state.get("evidence") or []
    iterations = state.get("review_iterations") or 0

    review = await _check_criteria(findings, evidence)

    if not review["approved"] and iterations < _MAX_REVIEW_ITERATIONS:
        logger.info("finding_reviewer: criteria failed, requesting re-correlation. feedback=%s", review["feedback"])
        return {"reviewer_feedback": review["feedback"]}

    high_sev = [f for f in findings if f.severity in ("critical", "high")]
    if high_sev:
        assistant_msg = _format_findings_for_review(high_sev)
        created_at = datetime.now(UTC).isoformat()

        await dao.push_artifact_message(job_id, FINDING_REVIEWER, {
            "role": "assistant",
            "content": assistant_msg,
            "created_at": created_at,
        })
        await dao.update_artifact_data(job_id, FINDING_REVIEWER, {
            "data": {"risk_findings": [f.__dict__ for f in high_sev]}
        })

        user_input: str = interrupt({
            "risk_findings": [f.__dict__ for f in high_sev],
            "assistant_message": assistant_msg,
        })

        await dao.push_artifact_message(job_id, FINDING_REVIEWER, {
            "role": "human",
            "content": user_input,
            "created_at": datetime.now(UTC).isoformat(),
            "action": "approve",
        })

        logger.info("finding_reviewer: HITL gate 2 — user acknowledged high-severity findings")
        return {
            "review_approved": True,
            "messages": [AIMessage(content=assistant_msg), HumanMessage(content=user_input)],
        }

    return {"review_approved": True}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/nodes/test_finding_reviewer.py -v
```

Expected: all passing, including the three original `_check_criteria` tests and the two new tests.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/unit/ tests/architecture/ -v
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/nodes/finding_reviewer.py tests/unit/nodes/test_finding_reviewer.py
git commit -m "feat: store HITL chat history on finding_reviewer artifact"
```
