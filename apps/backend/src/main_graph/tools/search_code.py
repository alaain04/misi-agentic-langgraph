from __future__ import annotations

import logging
import os
import shlex

from langchain_core.tools import tool

from src.domain.ports.container_run_port import ContainerRunPort

logger = logging.getLogger(__name__)

_SOURCE_EXTENSIONS = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".mts",
    ".cts",
}

# Manifest/lockfiles trivially mention every dependency name and aren't "usage"
# in any business-relevant sense - a dependency being listed in package.json is
# a given, not a finding. Exclude them from the source index so code_impact
# and search_code only surface real import/call sites.
_NON_SOURCE_BASENAMES = {
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "tsconfig.json",
}

_MAX_FILES = 200
_MAX_FILE_BYTES = 50_000
_SNIPPET_RADIUS = 150


def is_indexable_source_file(basename: str) -> bool:
    if basename in _NON_SOURCE_BASENAMES:
        return False
    return os.path.splitext(basename)[1] in _SOURCE_EXTENSIONS


def _walk_source_files(repo_path: str) -> list[tuple[str, str]]:
    """Return list of (relative_path, content) for source files."""
    results = []
    skip_dirs = {"node_modules", ".git", "dist", "build", ".next", "coverage"}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if not is_indexable_source_file(fname):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, repo_path)
            try:
                size = os.path.getsize(full)
                if size > _MAX_FILE_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                results.append((rel, content))
                if len(results) >= _MAX_FILES:
                    return results
            except OSError:
                continue
    return results


def _snippet_around_match(content: str, needle: str) -> str:
    idx = content.find(needle)
    if idx == -1:
        return content[:300]
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(content), idx + len(needle) + _SNIPPET_RADIUS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def find_local_usage_sites(repo_path: str, package_name: str) -> list[dict]:
    """Local substring scan for files that literally mention package_name.

    Independent of the codegraph structural index on purpose: this exists as
    a fallback for when codegraph's parser misses a usage (e.g. a dynamic
    require or a non-source config file), so it must not share codegraph's
    failure mode.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return []
    return [
        {"file": rel_path, "snippet": _snippet_around_match(content, package_name)}
        for rel_path, content in _walk_source_files(repo_path)
        if package_name in content
    ]


def make_search_code_tool(repo_path: str, container: ContainerRunPort, image: str):
    @tool
    async def search_code(query: str, top_k: int = 10) -> dict:
        """Search the repository for code patterns, imports, or package usage
        using natural language. Returns relevant symbols' source and call
        graph."""
        command = (
            f"codegraph explore {shlex.quote(query)} -p /workspace --max-files {top_k}"
        )
        rc, stdout, stderr = await container.run(
            image=image,
            command=command,
            volume=f"{repo_path}:/workspace",
            run_as_root=True,
        )
        if rc != 0:
            return {
                "query": query,
                "available": False,
                "error": stderr[:300] or f"exit {rc}",
            }

        return {"query": query, "available": True, "result": stdout}

    return search_code
