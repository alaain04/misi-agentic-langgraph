import json
from unittest.mock import AsyncMock

import pytest

from src.main_graph.tools.trivy_cli import (
    trivy_license_scan,
    trivy_sbom_scan,
    trivy_vuln_scan,
)


def _container(stdout: str = "", stderr: str = "", rc: int = 0) -> AsyncMock:
    container = AsyncMock()
    container.run.return_value = (rc, stdout, stderr)
    return container


@pytest.mark.asyncio
async def test_trivy_sbom_scan_runs_cyclonedx_no_scanners():
    doc = {"bomFormat": "CycloneDX", "components": []}
    container = _container(stdout=json.dumps(doc))
    await trivy_sbom_scan(repo_path="/tmp/repo", container=container)

    _, kwargs = container.run.call_args
    assert kwargs["command"] == "trivy fs --format cyclonedx /workspace"
    assert kwargs["volume"] == "/tmp/repo:/workspace"
    assert kwargs["cache_volume"] is not None
    assert kwargs["cache_volume"].endswith(":/root/.cache/trivy")


@pytest.mark.asyncio
async def test_trivy_sbom_scan_returns_document_on_success():
    doc = {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []}
    container = _container(stdout=json.dumps(doc))
    result = await trivy_sbom_scan(repo_path="/tmp/repo", container=container)
    assert result == doc


@pytest.mark.asyncio
async def test_trivy_sbom_scan_surfaces_error_when_binary_missing():
    container = _container(stdout="", stderr="sh: trivy: not found", rc=127)
    result = await trivy_sbom_scan(repo_path="/tmp/repo", container=container)
    assert "error" in result
    assert "trivy: not found" in result["error"]


@pytest.mark.asyncio
async def test_trivy_vuln_scan_runs_vuln_scanner_only():
    container = _container(stdout=json.dumps({"SchemaVersion": 2, "Results": []}))
    await trivy_vuln_scan(repo_path="/tmp/repo", container=container)

    _, kwargs = container.run.call_args
    assert kwargs["command"] == "trivy fs --format json --scanners vuln /workspace"


@pytest.mark.asyncio
async def test_trivy_vuln_scan_surfaces_error_on_unparseable_output():
    container = _container(stdout="not json", stderr="", rc=0)
    result = await trivy_vuln_scan(repo_path="/tmp/repo", container=container)
    assert "error" in result


@pytest.mark.asyncio
async def test_trivy_vuln_scan_accepts_empty_results_as_success():
    container = _container(stdout=json.dumps({"SchemaVersion": 2, "Results": []}))
    result = await trivy_vuln_scan(repo_path="/tmp/repo", container=container)
    assert "error" not in result
    assert result["Results"] == []


@pytest.mark.asyncio
async def test_trivy_license_scan_runs_license_scanner_only():
    container = _container(stdout=json.dumps({"SchemaVersion": 2, "Results": []}))
    await trivy_license_scan(repo_path="/tmp/repo", container=container)

    _, kwargs = container.run.call_args
    assert kwargs["command"] == "trivy fs --format json --scanners license /workspace"


@pytest.mark.asyncio
async def test_trivy_license_scan_surfaces_error_on_exec_failure():
    container = AsyncMock()
    container.run.side_effect = Exception("container failed")
    result = await trivy_license_scan(repo_path="/tmp/repo", container=container)
    assert "error" in result
