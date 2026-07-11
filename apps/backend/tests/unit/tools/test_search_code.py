from __future__ import annotations
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def test_make_search_code_tool_returns_tool():
    from src.main_graph.tools.search_code import make_search_code_tool
    tool = make_search_code_tool("vs-test")
    assert tool.name == "search_code"


def test_make_code_impact_tool_returns_tool():
    from src.main_graph.tools.code_impact import make_code_impact_tool
    tool = make_code_impact_tool("vs-test")
    assert tool.name == "code_impact"


@pytest.mark.asyncio
async def test_index_repository_writes_vector_store_id():
    from src.main_graph.subgraphs.discovery.nodes.index_repository import index_repository
    from src.main_graph.tools.search_code import get_vector_store

    with tempfile.TemporaryDirectory() as tmpdir:
        # create a fake source file
        (open(os.path.join(tmpdir, "index.ts"), "w")).write(
            'import express from "express";\nconst app = express();'
        )
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

        with patch("src.main_graph.subgraphs.discovery.nodes.index_repository._embeddings", mock_embeddings):
            result = await index_repository({"repo_path": tmpdir, "job_id": "j1"})

    assert "vector_store_id" in result
    store = get_vector_store(result["vector_store_id"])
    assert store is not None
