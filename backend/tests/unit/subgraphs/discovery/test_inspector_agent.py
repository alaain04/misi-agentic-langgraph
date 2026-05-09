import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.inspector_agent import (
    list_dir,
    read_file,
    inspector_agent,
)


# --- Tool unit tests (real filesystem) ---

def test_list_dir_returns_entries():
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "package.json"), "w").close()
        open(os.path.join(tmp, "yarn.lock"), "w").close()
        result = list_dir.invoke({"path": tmp})
        assert "package.json" in result
        assert "yarn.lock" in result


def test_list_dir_missing_path():
    result = list_dir.invoke({"path": "/nonexistent/path/xyz"})
    assert "not found" in result.lower() or "error" in result.lower()


def test_read_file_returns_content():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write('{"name": "my-app"}')
        path = f.name
    try:
        result = read_file.invoke({"path": path})
        assert "my-app" in result
    finally:
        os.unlink(path)


def test_read_file_missing_file():
    result = read_file.invoke({"path": "/nonexistent/file.json"})
    assert "not found" in result.lower() or "error" in result.lower()


# --- Node integration test (mocked agent) ---

@pytest.mark.asyncio
async def test_inspector_agent_maps_structured_response_to_state():
    from src.main_graph.subgraphs.discovery.nodes.inspector_agent import InspectorResult

    mock_output = InspectorResult(
        detected_package_manager="yarn",
        lock_file_missing=False,
        manifest_files=["package.json", "yarn.lock"],
        docker_image="node:lts-alpine",
        install_command="yarn install",
    )

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.inspector_agent.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_output})
        mock_factory.return_value = mock_agent
        result = await inspector_agent(
            {"repo_url": "https://github.com/x/y", "concern": "security", "job_id": "1", "repo_path": "/tmp/repo"}
        )

    assert result["detected_package_manager"] == "yarn"
    assert result["lock_file_missing"] is False
    assert result["manifest_files"] == ["package.json", "yarn.lock"]
    assert result["docker_image"] == "node:lts-alpine"
    assert result["install_command"] == "yarn install"


@pytest.mark.asyncio
async def test_inspector_agent_returns_error_when_no_repo_path():
    result = await inspector_agent(
        {"repo_url": "https://github.com/x/y", "concern": "security", "job_id": "1"}
    )
    assert "discovery_error" in result


@pytest.mark.asyncio
async def test_inspector_agent_returns_error_on_agent_exception():
    with patch(
        "src.main_graph.subgraphs.discovery.nodes.inspector_agent.create_agent"
    ) as mock_factory:
        mock_factory.side_effect = RuntimeError("LLM unavailable")
        result = await inspector_agent(
            {"repo_url": "https://github.com/x/y", "concern": "security", "job_id": "1", "repo_path": "/tmp/repo"}
        )

    assert "discovery_error" in result
    assert "Inspector agent failed" in result["discovery_error"]
