import json
from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.generate_sbom import generate_sbom

_BASE_STATE = {
    "repo_url": "https://github.com/test/repo",
    "concern": "",
    "job_id": "1",
    "repo_path": "/tmp/repo",
    "detected_package_manager": "npm",
    "docker_image": "node:22-alpine",
}

_SAMPLE_SBOM = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.4",
    "components": [{"name": "express", "version": "4.18.2"}],
}

_SBOM_JSON = json.dumps(_SAMPLE_SBOM)


def _docker_ok(stdout: str = _SBOM_JSON) -> str:
    return json.dumps({"returncode": 0, "stdout": stdout, "stderr": ""})


def _docker_fail(stderr: str) -> str:
    return json.dumps({"returncode": 1, "stdout": "", "stderr": stderr})


@pytest.mark.asyncio
async def test_generate_sbom_npm_success():
    with (
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_docker_command"
        ) as mock_cmd,
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao"
        ) as mock_dao,
        patch("pathlib.Path.exists", return_value=True),
    ):
        mock_cmd.ainvoke = AsyncMock(return_value=_docker_ok())
        mock_dao.save = AsyncMock(return_value="abc123")
        result = await generate_sbom(_BASE_STATE)

    mock_cmd.ainvoke.assert_awaited_once_with(
        {
            "image": "node:22-alpine",
            "command": "npm sbom --sbom-format=cyclonedx --package-lock-only",
            "workspace": "/tmp/repo",
        }
    )
    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM
    assert result["sbom_result_id"] == "abc123"
    assert "sbom_error" not in result


@pytest.mark.asyncio
async def test_generate_sbom_pnpm_success():
    state = {
        **_BASE_STATE,
        "detected_package_manager": "pnpm",
        "docker_image": "node:20-alpine",
    }

    with (
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_docker_command"
        ) as mock_cmd,
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao"
        ) as mock_dao,
        patch("pathlib.Path.exists", return_value=True),
    ):
        mock_cmd.ainvoke = AsyncMock(return_value=_docker_ok())
        mock_dao.save = AsyncMock(return_value="abc123")
        result = await generate_sbom(state)

    mock_cmd.ainvoke.assert_awaited_once_with(
        {
            "image": "node:20-alpine",
            "command": "pnpm sbom --sbom-format=cyclonedx --package-lock-only",
            "workspace": "/tmp/repo",
        }
    )
    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM


@pytest.mark.asyncio
async def test_generate_sbom_lts_image_is_allowed():
    state = {**_BASE_STATE, "docker_image": "node:lts-alpine"}

    with (
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_docker_command"
        ) as mock_cmd,
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao"
        ) as mock_dao,
        patch("pathlib.Path.exists", return_value=True),
    ):
        mock_cmd.ainvoke = AsyncMock(return_value=_docker_ok())
        mock_dao.save = AsyncMock(return_value="abc123")
        result = await generate_sbom(state)

    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM
    assert "sbom_error" not in result


@pytest.mark.asyncio
async def test_generate_sbom_rejects_node18():
    state = {**_BASE_STATE, "docker_image": "node:18-alpine"}

    with (
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao"
        ) as mock_dao,
        patch("pathlib.Path.exists", return_value=False),
    ):
        mock_dao.save = AsyncMock(return_value="err123")
        result = await generate_sbom(state)

    assert result["sbom_cyclonedx"] == {}
    assert "18" in result["sbom_error"] or "node:20+" in result["sbom_error"]
    assert result["sbom_result_id"] == "err123"


@pytest.mark.asyncio
async def test_generate_sbom_command_failure_saves_error():
    with (
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_docker_command"
        ) as mock_cmd,
        patch(
            "src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao"
        ) as mock_dao,
        patch("pathlib.Path.exists", return_value=True),
    ):
        mock_cmd.ainvoke = AsyncMock(
            return_value=_docker_fail("npm: unknown command: sbom")
        )
        mock_dao.save = AsyncMock(return_value="err456")
        result = await generate_sbom(_BASE_STATE)

    assert result["sbom_cyclonedx"] == {}
    assert "unknown command" in result["sbom_error"]
    assert result["sbom_result_id"] == "err456"


@pytest.mark.asyncio
async def test_generate_sbom_no_repo_path():
    state = {"repo_url": "", "concern": "", "repo_path": ""}

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao"
    ) as mock_dao:
        mock_dao.save = AsyncMock(return_value="no-path-id")
        result = await generate_sbom(state)

    assert result["sbom_error"] == "repo_path not available"
    assert result["sbom_cyclonedx"] == {}
