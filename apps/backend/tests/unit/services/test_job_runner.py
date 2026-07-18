from unittest.mock import AsyncMock, MagicMock, patch

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
        await run_analysis(
            "job-1", "https://github.com/x/y", "security", autopilot=False, dao=dao
        )

    dao.mark_failed.assert_awaited_once_with("job-1", error="graph exploded")


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
        mock_graph.aget_state = AsyncMock(
            return_value=MagicMock(
                values={"report_result_id": "r-1", "prep_result_id": "p-1"}
            )
        )
        await run_analysis(
            "job-2", "https://github.com/x/y", "security", autopilot=False, dao=dao
        )

    dao.update_artifact_data.assert_any_call("job-2", "prep", {"cost": 0.01})
    dao.update_artifact_data.assert_any_call("job-2", "analysis", {"cost": 0.02})
    dao.update_artifact_data.assert_any_call("job-2", "report", {"cost": 0.04})
