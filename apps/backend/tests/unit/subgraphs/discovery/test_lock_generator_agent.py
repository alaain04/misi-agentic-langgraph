from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import (
    LockGenResult,
    lock_generator_agent,
)


@pytest.mark.asyncio
async def test_lock_generator_agent_success():
    mock_output = LockGenResult(success=True, attempts=2, error=None)
    mock_docker_tool = MagicMock()
    config = {"configurable": {"docker_tool": mock_docker_tool}}

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.lock_generator_agent.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(
            return_value={"structured_response": mock_output}
        )
        mock_factory.return_value = mock_agent

        result = await lock_generator_agent(
            {
                "repo_url": "https://github.com/x/y",
                "concern": "security",
                "job_id": "1",
                "repo_path": "/tmp/repo",
                "detected_package_manager": "npm",
                "docker_image": "node:lts-alpine",
                "install_command": "npm install",
            },
            config,
        )

    assert result["lock_generation_attempts"] == 2
    assert result.get("lock_generation_error") is None


@pytest.mark.asyncio
async def test_lock_generator_agent_records_error_on_exhaustion():
    mock_output = LockGenResult(
        success=False, attempts=6, error="peer conflict unresolved"
    )
    mock_docker_tool = MagicMock()
    config = {"configurable": {"docker_tool": mock_docker_tool}}

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.lock_generator_agent.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(
            return_value={"structured_response": mock_output}
        )
        mock_factory.return_value = mock_agent

        result = await lock_generator_agent(
            {
                "repo_url": "https://github.com/x/y",
                "concern": "security",
                "job_id": "1",
                "repo_path": "/tmp/repo",
                "detected_package_manager": "npm",
                "docker_image": "node:lts-alpine",
                "install_command": "npm install",
            },
            config,
        )

    assert result["lock_generation_attempts"] == 6
    assert result["lock_generation_error"] == "peer conflict unresolved"
