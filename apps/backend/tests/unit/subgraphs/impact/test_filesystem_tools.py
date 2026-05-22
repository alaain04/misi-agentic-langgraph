import json
import os
import tempfile

from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.filesystem import (
    find_usages,
    list_source_files,
    read_file_excerpt,
)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_list_source_files_returns_js_ts_files():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "src", "app.ts"), "export {}")
        _write(os.path.join(tmp, "src", "index.js"), "")
        _write(os.path.join(tmp, "README.md"), "")
        result = list_source_files.invoke({"repo_path": tmp})
        assert "app.ts" in result
        assert "index.js" in result
        assert "README.md" not in result


def test_list_source_files_excludes_node_modules():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "node_modules", "lodash", "index.js"), "")
        _write(os.path.join(tmp, "src", "app.ts"), "")
        result = list_source_files.invoke({"repo_path": tmp})
        assert "node_modules" not in result
        assert "app.ts" in result


def test_find_usages_detects_es6_import():
    with tempfile.TemporaryDirectory() as tmp:
        _write(
            os.path.join(tmp, "src", "app.ts"),
            "import express from 'express';\n",
        )
        result = json.loads(find_usages.invoke({"dep_name": "express", "repo_path": tmp}))
        assert len(result) == 1
        assert result[0]["line"] == 1
        assert "express" in result[0]["statement"]


def test_find_usages_detects_require():
    with tempfile.TemporaryDirectory() as tmp:
        _write(
            os.path.join(tmp, "index.js"),
            "const _ = require('lodash');\n",
        )
        result = json.loads(find_usages.invoke({"dep_name": "lodash", "repo_path": tmp}))
        assert len(result) == 1


def test_find_usages_detects_subpath_import():
    with tempfile.TemporaryDirectory() as tmp:
        _write(
            os.path.join(tmp, "index.js"),
            "const map = require('lodash/map');\n",
        )
        result = json.loads(find_usages.invoke({"dep_name": "lodash", "repo_path": tmp}))
        assert len(result) == 1


def test_find_usages_excludes_node_modules():
    with tempfile.TemporaryDirectory() as tmp:
        _write(
            os.path.join(tmp, "node_modules", "express", "index.js"),
            "const x = require('express');\n",
        )
        _write(os.path.join(tmp, "src", "app.ts"), "")
        result = json.loads(find_usages.invoke({"dep_name": "express", "repo_path": tmp}))
        assert len(result) == 0


def test_read_file_excerpt_returns_lines_around_target():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
        f.write("\n".join(f"line_{i:02d}" for i in range(1, 21)))
        path = f.name
    try:
        result = read_file_excerpt.invoke({"path": path, "around_line": 10, "context": 2})
        assert "line_10" in result
        assert "line_08" in result
        assert "line_12" in result
        assert "line_01" not in result  # Check that line 1 is not included
    finally:
        os.unlink(path)


def test_read_file_excerpt_handles_missing_file():
    result = read_file_excerpt.invoke({"path": "/nonexistent/file.ts", "around_line": 5})
    assert "Error" in result or "error" in result
