from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.discovery.nodes.build_dependency_summary import (
    build_project_context,
)


@pytest.mark.asyncio
async def test_build_project_context_passes_container_and_cache():
    from src.main_graph.subgraphs.discovery.nodes import (
        build_dependency_summary as mod,
    )

    state = {
        "repo_path": "/tmp/repo",
        "concern": "check for vulnerabilities",
        "detected_package_manager": "npm",
        "repo_url": "https://github.com/x/y",
        "commit_sha": "sha1",
    }
    container = AsyncMock()
    cache = AsyncMock()
    config = {
        "configurable": {
            "services": {"container": container, "input_cache": cache}
        }
    }

    graph_mock = AsyncMock(
        return_value={"direct": {"express": "4.18.0"}, "packages": {}}
    )
    llm_response = MagicMock(content="a summary")
    pkg = {"name": "test-app"}

    with (
        patch.object(mod, "build_dependency_graph", graph_mock),
        patch.object(
            mod,
            "get_services",
            return_value={"container": container, "input_cache": cache},
        ),
        patch.object(mod, "read_package_json", return_value=pkg),
        patch.object(mod, "_llm") as llm_mock,
    ):
        llm_mock.ainvoke = AsyncMock(return_value=llm_response)
        result = await build_project_context(state, config)

    graph_mock.assert_awaited_once()
    _, kwargs = graph_mock.call_args
    assert kwargs["container"] is container
    assert kwargs["cache"] is cache
    assert result["project_metadata"]["direct_dependencies_count"] == 1


@pytest.mark.asyncio
async def test_build_project_context_skips_scan_on_discovery_error():
    state = {"discovery_error": "clone failed"}
    result = await build_project_context(state, config={})
    assert result["project_context"] == "Discovery failed: clone failed"
