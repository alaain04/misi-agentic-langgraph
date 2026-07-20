import json
from unittest.mock import AsyncMock

import pytest

from src.main_graph.tools.blast_radius import make_blast_radius_tool

# Captured from a real `codegraph impact <pkg> --json --depth N -p <path>`
# run (codegraph v1.4.1). Pinned so a future CLI upgrade that changes the
# output shape fails loudly instead of silently degrading to
# available=False everywhere.
_CAPTURED_CODEGRAPH_OUTPUT = {
    "symbol": "react",
    "depth": 2,
    "nodeCount": 4,
    "edgeCount": 0,
    "affected": [
        {
            "name": "react",
            "kind": "import",
            "filePath": "src/components/Header.tsx",
            "startLine": 1,
        },
        {
            "name": "react",
            "kind": "import",
            "filePath": "src/App.tsx",
            "startLine": 1,
        },
        {
            "name": "react",
            "kind": "import",
            "filePath": "test/App.test.tsx",
            "startLine": 2,
        },
    ],
}


def _container(stdout: str = "", stderr: str = "", rc: int = 0) -> AsyncMock:
    container = AsyncMock()
    container.run.return_value = (rc, stdout, stderr)
    return container


@pytest.mark.asyncio
async def test_blast_radius_tool_name():
    tool = make_blast_radius_tool("/tmp/repo", _container(), "codegraph-cli:latest")
    assert tool.name == "blast_radius"


@pytest.mark.asyncio
async def test_blast_radius_happy_path_classifies_prod_vs_test_files():
    container = _container(stdout=json.dumps(_CAPTURED_CODEGRAPH_OUTPUT))
    tool = make_blast_radius_tool("/tmp/repo", container, "codegraph-cli:latest")

    result = await tool.ainvoke({"package_name": "react"})

    assert result["available"] is True
    assert result["affected_file_count"] == 3
    assert result["production_file_count"] == 2
    assert result["isolated_to_tests_or_scripts"] is False
    assert "test/App.test.tsx:2" in result["affected_files"]
    assert result["node_count"] == 4

    _, kwargs = container.run.call_args
    assert kwargs["image"] == "codegraph-cli:latest"
    assert kwargs["volume"] == "/tmp/repo:/workspace"
    assert kwargs["run_as_root"] is True
    assert "codegraph impact react" in kwargs["command"]
    assert "--json" in kwargs["command"]


@pytest.mark.asyncio
async def test_blast_radius_detects_dot_spec_filename_as_test_file():
    """Regression: a *.spec.ts file with no "spec"/"test" *directory* segment
    must still be classified as a test file, not counted as production."""
    output = {
        **_CAPTURED_CODEGRAPH_OUTPUT,
        "affected": [
            {
                "name": "left-pad",
                "kind": "import",
                "filePath": "src/api/order/event/order-change.listener.ts",
                "startLine": 7,
            },
            {
                "name": "left-pad",
                "kind": "import",
                "filePath": "src/api/order/event/order-change.listener.spec.ts",
                "startLine": 9,
            },
        ],
    }
    container = _container(stdout=json.dumps(output))
    tool = make_blast_radius_tool("/tmp/repo", container, "codegraph-cli:latest")

    result = await tool.ainvoke({"package_name": "left-pad"})

    assert result["affected_file_count"] == 2
    assert result["production_file_count"] == 1
    assert result["isolated_to_tests_or_scripts"] is False


@pytest.mark.asyncio
async def test_blast_radius_isolated_to_tests_when_no_prod_files():
    output = {
        **_CAPTURED_CODEGRAPH_OUTPUT,
        "affected": [
            {
                "name": "left-pad",
                "kind": "import",
                "filePath": "scripts/build.js",
                "startLine": 1,
            }
        ],
    }
    container = _container(stdout=json.dumps(output))
    tool = make_blast_radius_tool("/tmp/repo", container, "codegraph-cli:latest")

    result = await tool.ainvoke({"package_name": "left-pad"})

    assert result["affected_file_count"] == 1
    assert result["production_file_count"] == 0
    assert result["isolated_to_tests_or_scripts"] is True


@pytest.mark.asyncio
async def test_blast_radius_nonzero_exit_returns_unavailable():
    container = _container(rc=1, stderr="CodeGraph not initialized in /workspace")
    tool = make_blast_radius_tool("/tmp/repo", container, "codegraph-cli:latest")

    result = await tool.ainvoke({"package_name": "left-pad"})

    assert result["available"] is False
    assert "not initialized" in result["error"]


@pytest.mark.asyncio
async def test_blast_radius_symbol_not_found_swallows_json_flag():
    """codegraph silently ignores --json and prints plain text with rc=0
    when the symbol/package isn't found — that's a real zero-usage answer
    (isolated dependency), not a tool error, so it must degrade to
    affected_file_count=0 rather than available=False."""
    container = _container(stdout="Symbol not found", rc=0)
    tool = make_blast_radius_tool("/tmp/repo", container, "codegraph-cli:latest")

    result = await tool.ainvoke({"package_name": "nonexistent-pkg"})

    assert result["available"] is True
    assert result["affected_file_count"] == 0
    assert result["affected_files"] == []


@pytest.mark.asyncio
async def test_blast_radius_valid_json_without_affected_key():
    """Defensive case: valid JSON dict that doesn't carry an "affected" key."""
    container = _container(stdout=json.dumps({"symbol": "left-pad"}), rc=0)
    tool = make_blast_radius_tool("/tmp/repo", container, "codegraph-cli:latest")

    result = await tool.ainvoke({"package_name": "left-pad"})

    assert result["available"] is True
    assert result["affected_file_count"] == 0
    assert result["affected_files"] == []


@pytest.mark.asyncio
async def test_blast_radius_malformed_json_returns_unavailable():
    container = _container(stdout="{not valid json", rc=0)
    tool = make_blast_radius_tool("/tmp/repo", container, "codegraph-cli:latest")

    result = await tool.ainvoke({"package_name": "left-pad"})

    assert result["available"] is False
    assert "error" in result
