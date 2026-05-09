from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.build_dependency_summary import (
    build_dependency_summary,
)

_SAMPLE_SBOM = {
    "metadata": {
        "component": {"name": "my-app", "bom-ref": "my-app"}
    },
    "components": [
        {"bom-ref": "pkg:npm/express@4.18.2", "name": "express", "version": "4.18.2"},
        {"bom-ref": "pkg:npm/accepts@1.3.8", "name": "accepts", "version": "1.3.8"},
    ],
    "dependencies": [
        {"ref": "my-app", "dependsOn": ["pkg:npm/express@4.18.2"]},
        {"ref": "pkg:npm/express@4.18.2", "dependsOn": ["pkg:npm/accepts@1.3.8"]},
    ],
}


@pytest.mark.asyncio
async def test_extracts_project_metadata_from_cyclonedx():
    state = {
        "sbom_cyclonedx": _SAMPLE_SBOM,
        "manifest_files": ["package.json", "pnpm-lock.yaml"],
        "concern": "security",
        "repo_url": "https://github.com/test/repo",
    }

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.build_dependency_summary._llm"
    ) as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="A great summary"))
        result = await build_dependency_summary(state)

    assert result["project_metadata"]["name"] == "my-app"
    assert result["project_metadata"]["package_manager"] == "pnpm"
    assert result["project_metadata"]["direct_dependencies_count"] == 1
    assert result["discovery_summary"] == "A great summary"


@pytest.mark.asyncio
async def test_returns_failure_summary_on_sbom_error():
    state = {
        "sbom_error": "Trivy timed out after 300s",
        "concern": "security",
        "repo_url": "",
    }

    result = await build_dependency_summary(state)

    assert result["project_metadata"]["name"] == "unknown"
    assert result["project_metadata"]["direct_dependencies_count"] == 0
    assert "Trivy timed out" in result["discovery_summary"]


@pytest.mark.asyncio
async def test_returns_failure_summary_on_discovery_error():
    state = {
        "discovery_error": "git clone failed",
        "concern": "security",
        "repo_url": "",
    }

    result = await build_dependency_summary(state)

    assert result["project_metadata"]["name"] == "unknown"
    assert "git clone failed" in result["discovery_summary"]
