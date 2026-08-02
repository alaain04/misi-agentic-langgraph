"""
Blackbox integration test for the discovery subgraph.

What is real:
- InMemoryVectorStore + text splitting (full pipeline)
- inspect_repo (reads fixture filesystem)
- save_prep_result (writes to real MongoDB via testcontainer)

What is mocked:
- container.run (no Docker needed for git clone)
- index_repository._embeddings (no OpenAI calls)
"""

from __future__ import annotations

import os
import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.graph import build_discovery_subgraph
from src.main_graph.subgraphs.discovery.nodes.save_prep_result import save_prep_result
from src.utils.config import settings

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "simple-nodejs-project")


def _setup_repo(job_id: str) -> str:
    """Copy fixture repo to the path clone_repo would create."""
    repo_path = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(repo_path, exist_ok=True)
    shutil.copytree(_FIXTURE, repo_path, dirs_exist_ok=True)
    return repo_path


def _make_embeddings_mock():
    """Return an embeddings mock that returns unit zero-vectors."""
    mock = MagicMock()
    mock.aembed_documents = AsyncMock(
        side_effect=lambda texts: [[0.0] * 1536 for _ in texts]
    )
    mock.aembed_query = AsyncMock(return_value=[0.0] * 1536)
    return mock


@pytest.mark.asyncio
async def test_discovery_produces_prep_result(subgraph_config, result_dao):
    """Happy path: fixture repo with lock file → PrepResult in MongoDB."""
    job_id = f"disc-{uuid.uuid4().hex[:8]}"
    repo_path = _setup_repo(job_id)

    try:
        with patch(
            "src.main_graph.subgraphs.discovery.nodes.index_repository._embeddings",
            _make_embeddings_mock(),
        ):
            graph = build_discovery_subgraph()
            result = await graph.ainvoke(
                {
                    "job_id": job_id,
                    "repo_url": "https://github.com/test/simple-nodejs-project",
                    "concern": "security vulnerabilities in dependencies",
                    "autopilot": True,
                },
                config=subgraph_config,
            )
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)

    assert result.get("prep_result_id"), "Expected prep_result_id in output state"

    prep = await result_dao.get_prep(result["prep_result_id"])
    assert prep.job_id == job_id
    assert prep.detected_package_manager == "npm"
    assert prep.project_metadata["name"] == "test-project"
    assert prep.project_metadata["direct_dependencies_count"] == 4
    assert "lodash" in prep.dependency_graph.get("direct", {})
    assert "express" in prep.dependency_graph.get("direct", {})


@pytest.mark.asyncio
async def test_discovery_sets_vector_store_id_when_sources_present(
    subgraph_config, result_dao
):
    """When the repo has source files, a vector_store_id is set in state."""
    job_id = f"disc-{uuid.uuid4().hex[:8]}"
    repo_path = _setup_repo(job_id)

    # Add a minimal JS source file so index_repository has something to embed
    with open(os.path.join(repo_path, "index.js"), "w") as f:
        f.write("const lodash = require('lodash');\nconsole.log('hello');\n")

    try:
        with patch(
            "src.main_graph.subgraphs.discovery.nodes.index_repository._embeddings",
            _make_embeddings_mock(),
        ):
            graph = build_discovery_subgraph()
            result = await graph.ainvoke(
                {
                    "job_id": job_id,
                    "repo_url": "https://github.com/test/simple-nodejs-project",
                    "concern": "supply chain",
                    "autopilot": True,
                },
                config=subgraph_config,
            )
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)

    assert result.get("prep_result_id")
    assert result.get("vector_store_id"), (
        "Expected non-empty vector_store_id when source files exist"
    )

    prep = await result_dao.get_prep(result["prep_result_id"])
    assert prep.vector_store_id == result["vector_store_id"]


@pytest.mark.asyncio
async def test_discovery_error_skips_prep_save(subgraph_config, result_dao):
    """When clone_repo fails, save_prep_result is skipped — no PrepResult in MongoDB."""
    job_id = f"disc-{uuid.uuid4().hex[:8]}"

    # Make the container return a non-zero exit code
    subgraph_config["configurable"]["container"].run = AsyncMock(
        return_value=(1, "", "fatal: repository not found")
    )

    with patch(
        "src.main_graph.subgraphs.discovery.nodes.index_repository._embeddings",
        _make_embeddings_mock(),
    ):
        graph = build_discovery_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "repo_url": "https://github.com/test/nonexistent",
                "concern": "security",
                "autopilot": True,
            },
            config=subgraph_config,
        )

    # discovery_error is set, so save_prep_result skips
    assert not result.get("prep_result_id"), (
        "Expected no prep_result_id when clone fails"
    )
    assert result.get("discovery_error")


@pytest.mark.asyncio
async def test_save_prep_result_calls_build_dependency_graph_with_container():
    """Test that save_prep_result passes container and trivy_image through."""
    state = {
        "job_id": "j1",
        "repo_path": "/tmp/repo",
        "repo_url": "https://github.com/x/y",
        "commit_sha": "sha1",
        "detected_package_manager": "npm",
        "docker_image": "aquasec/trivy:0.71.2",
    }
    dao = AsyncMock()
    dao.save_prep.return_value = "prep-id-1"
    container = AsyncMock()
    config = {"configurable": {"services": {"result_dao": dao, "container": container}}}

    graph_mock = AsyncMock(return_value={"direct": {}, "packages": {}})
    patch_graph = patch(
        "src.main_graph.subgraphs.discovery.nodes.save_prep_result."
        "build_dependency_graph",
        graph_mock,
    )
    patch_services = patch(
        "src.main_graph.subgraphs.discovery.nodes.save_prep_result.get_services",
        return_value={
            "result_dao": dao,
            "container": container,
            "input_cache": None,
        },
    )
    with patch_graph, patch_services:
        result = await save_prep_result(state, config)

    graph_mock.assert_awaited_once()
    _, kwargs = graph_mock.call_args
    assert kwargs["container"] is container
    assert kwargs["docker_image"] == settings.trivy_image
    assert result == {"prep_result_id": "prep-id-1"}
