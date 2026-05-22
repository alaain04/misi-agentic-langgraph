import json
from unittest.mock import AsyncMock, patch

import pytest

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort
from src.main_graph.subgraphs.discovery.service import generate_sbom_service

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


@pytest.mark.asyncio
async def test_generate_sbom_npm_success():
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(_SAMPLE_SBOM), "")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "abc123"

    with patch("pathlib.Path.exists", return_value=True):
        result = await generate_sbom_service(_BASE_STATE, container, dao)

    container.run.assert_awaited_once_with(
        "node:22-alpine",
        "npm sbom --sbom-format=cyclonedx --package-lock-only",
        "/tmp/repo:/workspace",
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
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(_SAMPLE_SBOM), "")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "abc123"

    with patch("pathlib.Path.exists", return_value=True):
        result = await generate_sbom_service(state, container, dao)

    container.run.assert_awaited_once_with(
        "node:20-alpine",
        "pnpm sbom --sbom-format=cyclonedx --package-lock-only",
        "/tmp/repo:/workspace",
    )
    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM


@pytest.mark.asyncio
async def test_generate_sbom_lts_image_is_allowed():
    state = {**_BASE_STATE, "docker_image": "node:lts-alpine"}
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(_SAMPLE_SBOM), "")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "abc123"

    with patch("pathlib.Path.exists", return_value=True):
        result = await generate_sbom_service(state, container, dao)

    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM
    assert "sbom_error" not in result


@pytest.mark.asyncio
async def test_generate_sbom_rejects_node18():
    state = {**_BASE_STATE, "docker_image": "node:18-alpine"}
    container = AsyncMock(spec=ContainerRunPort)
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "err123"

    with patch("pathlib.Path.exists", return_value=False):
        result = await generate_sbom_service(state, container, dao)

    container.run.assert_not_awaited()
    assert result["sbom_cyclonedx"] == {}
    assert "18" in result["sbom_error"]
    assert result["sbom_result_id"] == "err123"


@pytest.mark.asyncio
async def test_generate_sbom_command_failure_saves_error():
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (1, "", "npm: unknown command: sbom")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "err456"

    with patch("pathlib.Path.exists", return_value=True):
        result = await generate_sbom_service(_BASE_STATE, container, dao)

    assert result["sbom_cyclonedx"] == {}
    assert "unknown command" in result["sbom_error"]
    assert result["sbom_result_id"] == "err456"


@pytest.mark.asyncio
async def test_generate_sbom_no_repo_path():
    state = {"repo_url": "", "concern": "", "repo_path": ""}
    container = AsyncMock(spec=ContainerRunPort)
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "no-path-id"

    result = await generate_sbom_service(state, container, dao)

    assert result["sbom_error"] == "repo_path not available"
    assert result["sbom_cyclonedx"] == {}
