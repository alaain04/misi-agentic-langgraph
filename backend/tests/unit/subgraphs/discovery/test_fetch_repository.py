from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.fetch_repository import fetch_repository


@pytest.mark.asyncio
async def test_fetch_repository_returns_repo_path_on_success():
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b""))):
        result = await fetch_repository({"repo_url": "https://github.com/test/repo", "concern": ""})

    assert "repo_path" in result
    assert result["repo_path"].startswith("/")
    assert "package_json_content" not in result
    assert "lock_file_content" not in result
    assert "lock_file_name" not in result


@pytest.mark.asyncio
async def test_fetch_repository_error_on_empty_url():
    result = await fetch_repository({"repo_url": "", "concern": ""})
    assert result == {"discovery_error": "No repository URL provided"}


@pytest.mark.asyncio
async def test_fetch_repository_error_on_clone_failure():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"repository not found"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b"repository not found"))):
        result = await fetch_repository({"repo_url": "https://github.com/bad/url", "concern": ""})

    assert "discovery_error" in result
    assert "git clone failed" in result["discovery_error"]
