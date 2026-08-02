from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock

import pytest


def test_is_indexable_source_file_excludes_manifests_and_lockfiles():
    from src.main_graph.tools.search_code import is_indexable_source_file

    assert is_indexable_source_file("package.json") is False
    assert is_indexable_source_file("package-lock.json") is False
    assert is_indexable_source_file("pnpm-lock.yaml") is False
    assert is_indexable_source_file("tsconfig.json") is False
    assert is_indexable_source_file("index.ts") is True
    assert is_indexable_source_file("config.json") is True


def test_find_local_usage_sites_matches_literal_substring():
    from src.main_graph.tools.search_code import find_local_usage_sites

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "index.ts"), "w") as f:
            f.write('import express from "express";\nconst app = express();')
        with open(os.path.join(tmpdir, "other.ts"), "w") as f:
            f.write('import lodash from "lodash";')

        results = find_local_usage_sites(tmpdir, "express")

    assert [r["file"] for r in results] == ["index.ts"]
    assert "express" in results[0]["snippet"]


def test_find_local_usage_sites_missing_repo_path_returns_empty():
    from src.main_graph.tools.search_code import find_local_usage_sites

    assert find_local_usage_sites("", "express") == []
    assert find_local_usage_sites("/no/such/dir", "express") == []


@pytest.mark.asyncio
async def test_make_search_code_tool_returns_tool():
    from src.main_graph.tools.search_code import make_search_code_tool

    tool = make_search_code_tool("/repo", AsyncMock(), "codegraph-image")
    assert tool.name == "search_code"


@pytest.mark.asyncio
async def test_search_code_tool_runs_codegraph_explore():
    from src.main_graph.tools.search_code import make_search_code_tool

    container = AsyncMock()
    container.run.return_value = (0, "explore output", "")

    tool = make_search_code_tool("/repo", container, "codegraph-image")
    result = await tool.ainvoke({"query": "where is lodash used", "top_k": 5})

    assert result == {
        "query": "where is lodash used",
        "available": True,
        "result": "explore output",
    }
    _, kwargs = container.run.call_args
    assert "codegraph explore" in kwargs["command"]
    assert "--max-files 5" in kwargs["command"]
    assert kwargs["volume"] == "/repo:/workspace"


@pytest.mark.asyncio
async def test_search_code_tool_returns_unavailable_on_failure():
    from src.main_graph.tools.search_code import make_search_code_tool

    container = AsyncMock()
    container.run.return_value = (1, "", "boom")

    tool = make_search_code_tool("/repo", container, "codegraph-image")
    result = await tool.ainvoke({"query": "anything"})

    assert result == {"query": "anything", "available": False, "error": "boom"}
