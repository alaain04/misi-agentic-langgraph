from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.main_graph.constants import CONDUCTOR, HITL_GATE, TOOL_RUNNER
from src.models.job import JobStatus
from src.services.job_runner import _stream_graph, resume_analysis, run_analysis


def _make_dao() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_run_analysis_marks_failed_on_exception():
    dao = _make_dao()

    async def bad_stream(*args, **kwargs):
        raise RuntimeError("graph exploded")
        yield  # makes this an async generator

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
    ):
        mock_graph.astream = bad_stream
        await run_analysis("job-1", "https://github.com/x/y", "security", autopilot=False, dao=dao)

    dao.mark_failed.assert_awaited_once_with("job-1", error="graph exploded")


@pytest.mark.asyncio
async def test_run_analysis_sets_awaiting_approval_on_interrupt():
    dao = _make_dao()

    async def interrupt_stream(*args, **kwargs):
        interrupt = MagicMock()
        interrupt.value = {"question": "Approve?", "created_at": "t"}
        yield {"__interrupt__": [interrupt]}

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
    ):
        mock_graph.astream = interrupt_stream
        await run_analysis("job-1", "https://github.com/x/y", "security", autopilot=False, dao=dao)

    dao.update_status.assert_any_await("job-1", JobStatus.awaiting_approval)
    dao.save_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_analysis_marks_failed_on_exception():
    dao = _make_dao()

    async def bad_stream(*args, **kwargs):
        raise RuntimeError("resume exploded")
        yield  # makes this an async generator

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
    ):
        mock_graph.astream = bad_stream
        await resume_analysis("job-2", "approve", dao)

    dao.mark_failed.assert_awaited_once_with("job-2", error="resume exploded")


@pytest.mark.asyncio
async def test_stream_graph_hitl_artifact_lifecycle():
    """Interrupt sets HITL artifact to running + stores question; HITL_GATE node completes it."""
    dao = AsyncMock()
    job_id = "job-hitl"

    async def hitl_lifecycle_stream(*args, **kwargs):
        intr = MagicMock()
        intr.value = {"question": "Should I continue?", "type": "checkpoint"}
        yield {"__interrupt__": [intr]}
        yield {HITL_GATE: {}}

    mock_graph = MagicMock()
    mock_graph.astream = hitl_lifecycle_stream

    interrupted = await _stream_graph(mock_graph, {}, {}, dao, job_id)

    assert interrupted is True
    dao.start_artifact.assert_any_await(job_id, HITL_GATE)
    dao.push_artifact_message.assert_awaited_once_with(
        job_id,
        HITL_GATE,
        {
            "role": "assistant",
            "content": "Should I continue?",
            "created_at": ANY,
            "type": "checkpoint",
        },
    )
    dao.complete_artifact.assert_any_await(job_id, HITL_GATE, "done")


@pytest.mark.asyncio
async def test_stream_graph_conductor_tool_runner_accumulation():
    """CONDUCTOR chunk pushes conductor iteration; TOOL_RUNNER chunk pushes tool_runner iteration with correct conductor_iteration."""
    dao = AsyncMock()
    job_id = "job-accum"

    decision = MagicMock()
    decision.tool_calls = []
    decision.finalize = False
    decision.reasoning = "let's go"

    tool_result = MagicMock()
    tool_result.tool = "npm_audit"
    tool_result.error = None

    async def accumulation_stream(*args, **kwargs):
        yield {CONDUCTOR: {"conductor_iteration": 2, "conductor_decision": decision, "findings": []}}
        yield {TOOL_RUNNER: {"tool_results": [tool_result]}}

    mock_graph = MagicMock()
    mock_graph.astream = accumulation_stream

    await _stream_graph(mock_graph, {}, {}, dao, job_id)

    dao.push_artifact_item.assert_any_await(
        job_id,
        CONDUCTOR,
        "iterations",
        {
            "iteration": 2,
            "tool_calls": [],
            "findings_count": 0,
            "finalize": False,
            "reasoning": "let's go",
            "started_at": ANY,
        },
    )
    dao.push_artifact_item.assert_any_await(
        job_id,
        TOOL_RUNNER,
        "iterations",
        {
            "conductor_iteration": 2,
            "tools_run": ["npm_audit"],
            "errors": [],
            "started_at": ANY,
        },
    )


@pytest.mark.asyncio
async def test_stream_graph_saves_dep_tree_when_npm_list_succeeds():
    """When TOOL_RUNNER emits an npm_list result with no error, dep tree is saved."""
    dao = _make_dao()
    job_id = "job-deptree"

    npm_tree = {"name": "my-app", "dependencies": {"lodash": {"version": "4.17.21"}}}
    tool_result = MagicMock()
    tool_result.tool = "npm_list"
    tool_result.error = None
    tool_result.output = npm_tree

    async def tree_stream(*args, **kwargs):
        yield {TOOL_RUNNER: {"tool_results": [tool_result]}}

    mock_graph = MagicMock()
    mock_graph.astream = tree_stream

    await _stream_graph(mock_graph, {}, {}, dao, job_id)

    dao.save_dep_tree.assert_awaited_once_with(job_id, npm_tree)


@pytest.mark.asyncio
async def test_stream_graph_skips_dep_tree_when_npm_list_errors():
    """When npm_list result has an error, dep tree is not saved."""
    dao = _make_dao()
    job_id = "job-deptree-err"

    tool_result = MagicMock()
    tool_result.tool = "npm_list"
    tool_result.error = "npm command failed"
    tool_result.output = {}

    async def error_stream(*args, **kwargs):
        yield {TOOL_RUNNER: {"tool_results": [tool_result]}}

    mock_graph = MagicMock()
    mock_graph.astream = error_stream

    await _stream_graph(mock_graph, {}, {}, dao, job_id)

    dao.save_dep_tree.assert_not_awaited()
