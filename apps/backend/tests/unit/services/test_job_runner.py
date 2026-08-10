from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        patch("src.services.job_runner.get_input_cache"),
    ):
        mock_graph.astream = bad_stream
        await run_analysis(
            "job-1", "https://github.com/x/y", "security", autopilot=False, dao=dao
        )

    dao.mark_failed.assert_awaited_once_with("job-1", error="graph exploded")
    dao.save_cost.assert_awaited_once_with("job-1", 0.0)
    dao.save_cost_breakdown.assert_awaited_once_with("job-1", {})


@pytest.mark.asyncio
async def test_run_analysis_records_cost_per_subgraph():
    dao = _make_dao()

    async def fake_stream(*args, **kwargs):
        yield {"prep": {}}
        yield {"analysis": {"analysis_result_id": "ares-1"}}
        yield {"remediation": {"remediation_result_id": None}}

    fake_cost_cb = MagicMock()
    fake_cost_cb.cost = MagicMock(side_effect=[0.01, 0.03, 0.07, 0.07])
    fake_breakdown = {"planner": {"cost": 0.07, "call_count": 3}}
    fake_cost_cb.breakdown = MagicMock(return_value=fake_breakdown)

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.get_input_cache"),
        patch("src.services.job_runner.CostCallback", return_value=fake_cost_cb),
    ):
        mock_graph.astream = fake_stream
        mock_graph.aget_state = AsyncMock(
            return_value=MagicMock(
                values={"remediation_result_id": "r-1", "prep_result_id": "p-1"}
            )
        )
        await run_analysis(
            "job-2", "https://github.com/x/y", "security", autopilot=False, dao=dao
        )

    dao.update_artifact_data.assert_any_call("job-2", "prep", {"cost": 0.01})
    dao.update_artifact_data.assert_any_call("job-2", "analysis", {"cost": 0.02})
    dao.update_artifact_data.assert_any_call("job-2", "remediation", {"cost": 0.04})
    dao.save_cost.assert_awaited_once_with("job-2", 0.07)
    dao.save_cost_breakdown.assert_awaited_once_with("job-2", fake_breakdown)


@pytest.mark.asyncio
async def test_run_analysis_threads_github_token_into_configurable():
    dao = _make_dao()
    captured: dict = {}

    async def fake_stream(*args, **kwargs):
        captured["config"] = args[1]
        yield {"prep": {}}

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.get_input_cache"),
    ):
        mock_graph.astream = fake_stream
        mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={}))
        await run_analysis(
            "job-5",
            "https://github.com/x/y",
            "security",
            autopilot=False,
            dao=dao,
            github_token="ghp_abc123",
        )

    assert captured["config"]["configurable"]["github_token"] == "ghp_abc123"


@pytest.mark.asyncio
async def test_run_analysis_omits_github_token_when_not_provided():
    dao = _make_dao()
    captured: dict = {}

    async def fake_stream(*args, **kwargs):
        captured["config"] = args[1]
        yield {"prep": {}}

    with (
        patch("src.services.job_runner.main_graph") as mock_graph,
        patch("src.services.job_runner.clear_cache"),
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.get_input_cache"),
    ):
        mock_graph.astream = fake_stream
        mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={}))
        await run_analysis(
            "job-6", "https://github.com/x/y", "security", autopilot=False, dao=dao
        )

    assert "github_token" not in captured["config"]["configurable"]


def _noop_cb():
    from src.utils.cost import CostCallback

    return CostCallback()


def test_build_config_sets_remediate_and_git_pr():
    from src.services.job_runner import _build_config

    with (
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.get_input_cache"),
    ):
        cfg = _build_config("j1", dao=_make_dao(), cost_cb=_noop_cb(), remediate=True)
        assert cfg["configurable"]["remediate"] is True
        assert cfg["configurable"]["git_pr"] is not None


def test_build_config_remediate_defaults_false():
    from src.services.job_runner import _build_config

    with (
        patch("src.services.job_runner.get_result_dao"),
        patch("src.services.job_runner.get_input_cache"),
    ):
        cfg = _build_config("j1", dao=_make_dao(), cost_cb=_noop_cb())
        assert cfg["configurable"]["remediate"] is False
