from unittest.mock import AsyncMock, patch

import pytest

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.discovery.service import clone_repository_service


@pytest.mark.asyncio
async def test_clone_success_returns_repo_path():
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, "", "")

    with patch("os.makedirs"):
        result = await clone_repository_service(
            {"repo_url": "https://github.com/test/repo", "job_id": "99"},
            container,
        )

    assert "repo_path" in result
    assert "discovery_error" not in result


@pytest.mark.asyncio
async def test_clone_empty_url_returns_error():
    container = AsyncMock(spec=ContainerRunPort)

    result = await clone_repository_service(
        {"repo_url": "", "job_id": "99"},
        container,
    )

    assert result == {"discovery_error": "No repository URL provided"}
    container.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_clone_failure_returns_error():
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (1, "", "repository not found")

    with patch("os.makedirs"):
        result = await clone_repository_service(
            {"repo_url": "https://github.com/bad/repo", "job_id": "99"},
            container,
        )

    assert "discovery_error" in result
    assert "git clone failed" in result["discovery_error"]
