from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.nodes.execute_plan import execute_plan


def _make_state(subgraph_name: str = "unknown", job_id: str = "job-1") -> dict:
    return {
        "subgraph_name": subgraph_name,
        "job_id": job_id,
        "sbom_cyclonedx": {},
        "discovery_summary": "",
        "concern": "security",
        "upstream_results": {},
        "subgraph_results": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_execute_plan_unknown_subgraph_records_failure():
    mock_dao = AsyncMock()
    state = _make_state(subgraph_name="does_not_exist", job_id="job-1")

    with patch("src.main_graph.nodes.execute_plan._dao", mock_dao):
        result = await execute_plan(state)

    assert result["subgraph_results"][0]["error"] == "unknown subgraph"
    mock_dao.complete_artifact.assert_awaited_once_with("job-1", "does_not_exist", "failed")


@pytest.mark.asyncio
async def test_execute_plan_no_job_id_skips_artifact_tracking():
    mock_dao = AsyncMock()
    state = _make_state(subgraph_name="does_not_exist", job_id="")

    with patch("src.main_graph.nodes.execute_plan._dao", mock_dao):
        result = await execute_plan(state)

    assert result["subgraph_results"][0]["error"] == "unknown subgraph"
    mock_dao.complete_artifact.assert_not_awaited()
