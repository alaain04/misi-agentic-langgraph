from __future__ import annotations

from langchain_core.tools import tool

from src.main_graph.tools.search_code import _store_cache

_SNIPPET_RADIUS = 150


def _snippet_around_match(content: str, needle: str) -> str:
    """Return a window centered on `needle` rather than a blind prefix slice,
    so the snippet shown as evidence actually contains the matched usage."""
    idx = content.find(needle)
    if idx == -1:
        return content[:300]
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(content), idx + len(needle) + _SNIPPET_RADIUS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def make_code_impact_tool(vector_store_id: str):
    @tool
    async def code_impact(package_name: str) -> list[dict]:
        """Find source files that import or use a specific npm package."""
        store = _store_cache.get(vector_store_id)
        if store is None:
            return [{"error": f"Vector store {vector_store_id} not loaded"}]
        query = f"import {package_name} require {package_name}"
        results = await store.asimilarity_search(query, k=20)
        return [
            {
                "file": doc.metadata.get("file", ""),
                "snippet": _snippet_around_match(doc.page_content, package_name),
            }
            for doc in results
            if package_name in doc.page_content
        ]

    return code_impact
