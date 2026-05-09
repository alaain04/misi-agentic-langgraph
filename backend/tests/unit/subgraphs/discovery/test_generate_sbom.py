from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.generate_sbom import generate_sbom

_SAMPLE_SBOM = {
    "bomFormat": "CycloneDX",
    "metadata": {"component": {"name": "my-app", "bom-ref": "my-app"}},
    "components": [{"bom-ref": "pkg:npm/express@4.18.2", "name": "express", "version": "4.18.2"}],
    "dependencies": [{"ref": "my-app", "dependsOn": ["pkg:npm/express@4.18.2"]}],
}


@pytest.mark.asyncio
async def test_generate_sbom_success():
    state = {"repo_url": "https://github.com/test/repo", "concern": "", "repo_path": "/tmp/repo"}

    with patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_trivy",
               new=AsyncMock(return_value=(_SAMPLE_SBOM, ""))) as mock_trivy, \
         patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao") as mock_dao, \
         patch("pathlib.Path.exists", return_value=True):
        mock_dao.save = AsyncMock(return_value="abc123")
        result = await generate_sbom(state)

    mock_trivy.assert_awaited_once_with("/tmp/repo", "--format", "cyclonedx")
    assert result["sbom_cyclonedx"] == _SAMPLE_SBOM
    assert result["sbom_result_id"] == "abc123"
    assert "sbom_error" not in result


@pytest.mark.asyncio
async def test_generate_sbom_trivy_failure_still_saves():
    state = {"repo_url": "https://github.com/test/repo", "concern": "", "repo_path": "/tmp/repo"}

    with patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.run_trivy",
               new=AsyncMock(side_effect=RuntimeError("Trivy timed out after 300s"))), \
         patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao") as mock_dao, \
         patch("pathlib.Path.exists", return_value=False):
        mock_dao.save = AsyncMock(return_value="err123")
        result = await generate_sbom(state)

    assert result["sbom_cyclonedx"] == {}
    assert result["sbom_error"] == "Trivy timed out after 300s"
    assert result["sbom_result_id"] == "err123"
    mock_dao.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_sbom_no_repo_path():
    state = {"repo_url": "", "concern": "", "repo_path": ""}

    with patch("src.main_graph.subgraphs.discovery.nodes.generate_sbom.sbom_dao") as mock_dao:
        mock_dao.save = AsyncMock(return_value="no-path-id")
        result = await generate_sbom(state)

    assert result["sbom_error"] == "repo_path not available"
    assert result["sbom_cyclonedx"] == {}
