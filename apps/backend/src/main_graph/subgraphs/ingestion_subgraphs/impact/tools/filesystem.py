"""Filesystem tools for the impact analysis agent."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from langchain_core.tools import tool

_SOURCE_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")


@tool
def list_source_files(
    repo_path: str,
    extensions: list[str] = list(_SOURCE_EXTENSIONS),
) -> str:
    """Recursively list all source files in repo_path, excluding node_modules.

    Returns newline-separated absolute file paths.
    """
    found: list[str] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for fname in files:
            if any(fname.endswith(ext) for ext in extensions):
                found.append(os.path.join(root, fname))
    return "\n".join(found) if found else "(no source files found)"


@tool
def find_usages(dep_name: str, repo_path: str) -> str:
    """Find all import/require usages of dep_name in the project source files.

    Returns JSON: [{file, line, statement}]
    """
    escaped = re.escape(dep_name)
    # Matches: from 'dep', from "dep", require('dep'), require("dep"),
    # import('dep'), import("dep") — also subpath imports like 'dep/sub'
    pattern = re.compile(
        r"""(?:from\s+|require\s*\(\s*|import\s*\(\s*)['"]"""
        + escaped
        + r"""(?:['"/])""",
        re.MULTILINE,
    )
    usages: list[dict] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for fname in files:
            if not any(fname.endswith(ext) for ext in _SOURCE_EXTENSIONS):
                continue
            fpath = os.path.join(root, fname)
            try:
                text = Path(fpath).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    usages.append({"file": fpath, "line": i, "statement": line.strip()})
    return json.dumps(usages)


@tool
def read_file_excerpt(path: str, around_line: int, context: int = 5) -> str:
    """Read ±context lines around around_line from the file at path.

    Returns a formatted string with line numbers; marks the target line with >>>.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        return f"Error reading file: {exc}"
    start = max(0, around_line - 1 - context)
    end = min(len(lines), around_line + context)
    rows = []
    for i, line in enumerate(lines[start:end], start + 1):
        prefix = ">>>" if i == around_line else "   "
        rows.append(f"{prefix} {i:4d}: {line}")
    return "\n".join(rows)
