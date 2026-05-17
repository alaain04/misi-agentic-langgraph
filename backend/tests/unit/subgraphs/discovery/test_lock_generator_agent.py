import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import (
    lock_generator_agent,
    run_docker_command,
)

# --- Tool unit tests ---


@pytest.mark.asyncio
async def test_run_docker_command_returns_json_on_success():
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("asyncio.wait_for", new=AsyncMock(return_value=(b"ok", b""))),
    ):
        result = await run_docker_command.ainvoke(
            {
                "image": "node:lts-alpine",
                "command": "npm install",
                "volume": "/tmp/repo:/workspace",
            }
        )

    data = json.loads(result)
    assert data["returncode"] == 0
    assert "ok" in data["stdout"]


@pytest.mark.asyncio
async def test_run_docker_command_returns_json_on_failure():
    mock_proc = MagicMock()
    mock_proc.returncode = 1

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b"peer conflict"))),
    ):
        result = await run_docker_command.ainvoke(
            {
                "image": "node:lts-alpine",
                "command": "npm install",
                "volume": "/tmp/repo:/workspace",
            }
        )

    data = json.loads(result)
    assert data["returncode"] == 1
    assert "peer conflict" in data["stderr"]


# --- Node integration test (mocked agent) ---


@pytest.mark.asyncio
async def test_lock_generator_agent_success():
    from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import (
        LockGenResult,
    )

    mock_output = LockGenResult(success=True, attempts=2, error=None)

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
            }
        )

    assert result["lock_generation_attempts"] == 2
    assert result.get("lock_generation_error") is None


@pytest.mark.asyncio
async def test_lock_generator_agent_records_error_on_exhaustion():
    from src.main_graph.subgraphs.discovery.nodes.lock_generator_agent import (
        LockGenResult,
    )

    mock_output = LockGenResult(
        success=False, attempts=6, error="peer conflict unresolved"
    )

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
            }
        )

    assert result["lock_generation_attempts"] == 6
    assert result["lock_generation_error"] == "peer conflict unresolved"
