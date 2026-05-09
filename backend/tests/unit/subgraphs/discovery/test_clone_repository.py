from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.clone_repository import clone_repository


@pytest.mark.asyncio
async def test_clone_success_returns_repo_path():
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b""))):
        result = await clone_repository(
            {"repo_url": "https://github.com/test/repo", "concern": "security", "job_id": "99"}
        )

    assert "repo_path" in result
    assert "discovery_error" not in result


@pytest.mark.asyncio
async def test_clone_empty_url_returns_error():
    result = await clone_repository({"repo_url": "", "concern": "security", "job_id": "99"})
    assert result == {"discovery_error": "No repository URL provided"}


@pytest.mark.asyncio
async def test_clone_failure_returns_error():
    mock_proc = MagicMock()
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b"repository not found"))):
        result = await clone_repository(
            {"repo_url": "https://github.com/bad/repo", "concern": "security", "job_id": "99"}
        )

    assert "discovery_error" in result
    assert "git clone failed" in result["discovery_error"]
