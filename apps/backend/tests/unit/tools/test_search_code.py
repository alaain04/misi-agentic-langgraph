from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_make_search_code_tool_returns_tool():
    from src.main_graph.tools.search_code import make_search_code_tool

    tool = make_search_code_tool("vs-test")
    assert tool.name == "search_code"


def test_is_indexable_source_file_excludes_manifests_and_lockfiles():
    from src.main_graph.tools.search_code import is_indexable_source_file

    assert is_indexable_source_file("package.json") is False
    assert is_indexable_source_file("package-lock.json") is False
    assert is_indexable_source_file("pnpm-lock.yaml") is False
    assert is_indexable_source_file("tsconfig.json") is False
    assert is_indexable_source_file("index.ts") is True
    assert is_indexable_source_file("config.json") is True


@pytest.mark.asyncio
async def test_index_repository_excludes_package_json():
    from src.main_graph.subgraphs.discovery.nodes.index_repository import (
        _walk_source_files,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        (open(os.path.join(tmpdir, "index.ts"), "w")).write(
            'import express from "express";\nconst app = express();'
        )
        (open(os.path.join(tmpdir, "package.json"), "w")).write(
            '{"dependencies": {"express": "1.0.0"}}'
        )

        files = _walk_source_files(tmpdir)

    relpaths = [rel for rel, _ in files]
    assert "index.ts" in relpaths
    assert "package.json" not in relpaths


@pytest.mark.asyncio
async def test_index_repository_writes_vector_store_id():
    from src.main_graph.subgraphs.discovery.nodes.index_repository import (
        index_repository,
    )
    from src.main_graph.tools.search_code import get_vector_store

    with tempfile.TemporaryDirectory() as tmpdir:
        # create a fake source file
        (open(os.path.join(tmpdir, "index.ts"), "w")).write(
            'import express from "express";\nconst app = express();'
        )
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

        with patch(
            "src.main_graph.subgraphs.discovery.nodes.index_repository._embeddings",
            mock_embeddings,
        ):
            result = await index_repository({"repo_path": tmpdir, "job_id": "j1"})

    assert "vector_store_id" in result
    store = get_vector_store(result["vector_store_id"])
    assert store is not None
