from unittest.mock import AsyncMock

import pytest

from src.main_graph.subgraphs.discovery.nodes.index_codegraph import index_codegraph

_BASE_STATE = {
    "job_id": "test-job",
    "repo_url": "https://github.com/test/repo",
    "concern": "security",
    "autopilot": False,
}


def _config(container=None):
    return {"configurable": {"container": container or AsyncMock()}}


@pytest.mark.asyncio
async def test_index_codegraph_success(tmp_path):
    container = AsyncMock()
    container.run.return_value = (0, "", "")

    result = await index_codegraph(
        {**_BASE_STATE, "repo_path": str(tmp_path)}, _config(container=container)
    )

    assert result == {"codegraph_ready": True}
    container.run.assert_awaited_once()
    _, kwargs = container.run.call_args
    assert kwargs["run_as_root"] is True
    assert "codegraph init" in kwargs["command"]
    assert kwargs["volume"] == f"{tmp_path}:/workspace"


@pytest.mark.asyncio
async def test_index_codegraph_failure_sets_not_ready(tmp_path):
    container = AsyncMock()
    container.run.return_value = (1, "", "some error")

    result = await index_codegraph(
        {**_BASE_STATE, "repo_path": str(tmp_path)}, _config(container=container)
    )

    assert result == {"codegraph_ready": False}


@pytest.mark.asyncio
async def test_index_codegraph_skips_when_repo_path_missing():
    container = AsyncMock()

    result = await index_codegraph(_BASE_STATE, _config(container=container))

    assert result == {"codegraph_ready": False}
    container.run.assert_not_awaited()
