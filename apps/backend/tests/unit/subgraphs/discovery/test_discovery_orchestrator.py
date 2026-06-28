from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator import (
    OrchestratorResult,
    discovery_orchestrator,
)

_SAMPLE_SBOM = {"bomFormat": "CycloneDX", "components": []}

_BASE_STATE = {
    "job_id": "test-job",
    "repo_url": "https://github.com/test/repo",
    "concern": "security",
}

_SUCCESS_RESULT = OrchestratorResult(
    repo_path="/tmp/debug_job_test-job",
    detected_package_manager="npm",
    package_manager_version="latest",
    manifest_files=["package.json", "package-lock.json"],
    docker_image="node:22-alpine",
    sbom_cyclonedx=_SAMPLE_SBOM,
    sbom_error=None,
    discovery_error=None,
)


def _config(sbom_dao):
    return {"configurable": {"sbom_dao": sbom_dao, "docker_tool": MagicMock()}}


@pytest.mark.asyncio
async def test_success_writes_all_state_fields():
    dao = AsyncMock()
    dao.save.return_value = "result-id-1"

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"structured_response": _SUCCESS_RESULT}
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM
    assert result["sbom_result_id"] == "result-id-1"
    assert result["detected_package_manager"] == "npm"
    assert result["docker_image"] == "node:22-alpine"
    assert result["manifest_files"] == ["package.json", "package-lock.json"]
    assert "discovery_error" not in result
    assert "sbom_error" not in result
    dao.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_sbom_error_included_in_state():
    dao = AsyncMock()
    dao.save.return_value = "err-id"
    error_result = OrchestratorResult(
        repo_path="/tmp/debug_job_test-job",
        detected_package_manager="npm",
        package_manager_version="latest",
        manifest_files=["package.json"],
        docker_image="node:22-alpine",
        sbom_cyclonedx={},
        sbom_error="ERESOLVE: could not resolve dependency tree",
        discovery_error=None,
    )

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"structured_response": error_result}
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert result["sbom_cyclonedx"] == {}
    assert result["sbom_error"] == "ERESOLVE: could not resolve dependency tree"
    assert result["sbom_result_id"] == "err-id"
    assert "discovery_error" not in result


@pytest.mark.asyncio
async def test_discovery_error_included_in_state():
    dao = AsyncMock()
    dao.save.return_value = "clone-fail-id"
    clone_fail_result = OrchestratorResult(
        repo_path="/tmp/debug_job_test-job",
        detected_package_manager="npm",
        package_manager_version="latest",
        manifest_files=[],
        docker_image="node:lts-alpine",
        sbom_cyclonedx={},
        sbom_error=None,
        discovery_error="git clone failed: repository not found",
    )

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"structured_response": clone_fail_result}
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert result["discovery_error"] == "git clone failed: repository not found"
    assert result["sbom_cyclonedx"] == {}


@pytest.mark.asyncio
async def test_agent_exception_returns_discovery_error():
    dao = AsyncMock()
    dao.save.return_value = "crash-id"

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.discovery_orchestrator.create_agent"
    ) as mock_create:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = RuntimeError("agent crashed")
        mock_create.return_value = mock_agent

        result = await discovery_orchestrator(_BASE_STATE, _config(dao))

    assert "discovery_error" in result
    assert "agent crashed" in result["discovery_error"]
    assert result["sbom_cyclonedx"] == {}
    assert result["sbom_result_id"] == "crash-id"
    dao.save.assert_awaited_once()
