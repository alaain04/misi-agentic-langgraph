from unittest.mock import AsyncMock, patch

import pytest

from src.models.job import JobStatus
from src.services.job_runner import run_analysis


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
        patch("src.services.job_runner.get_result_dao"),
    ):
        mock_graph.astream = bad_stream
        await run_analysis("job-1", "https://github.com/x/y", "security", autopilot=False, dao=dao)

    dao.mark_failed.assert_awaited_once_with("job-1", error="graph exploded")
