from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.job import JobStatus
from src.services.job_runner import resume_analysis, run_analysis


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
        patch("src.services.job_runner.delete_store"),
    ):
        mock_graph.astream = bad_stream
        await run_analysis("job-1", "https://github.com/x/y", "security", dao)

    dao.mark_failed.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_run_analysis_sets_awaiting_approval_on_interrupt():
    dao = _make_dao()

    async def interrupt_stream(*args, **kwargs):
        interrupt = MagicMock()
        interrupt.value = {"question": "Approve?", "created_at": "t"}
        yield {"__interrupt__": [interrupt]}

    with patch("src.services.job_runner.main_graph") as mock_graph:
        mock_graph.astream = interrupt_stream
        await run_analysis("job-1", "https://github.com/x/y", "security", dao)

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
        patch("src.services.job_runner.delete_store"),
    ):
        mock_graph.astream = bad_stream
        await resume_analysis("job-2", "approve", dao)

    dao.mark_failed.assert_awaited_once_with("job-2")
