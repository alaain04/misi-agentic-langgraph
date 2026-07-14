from __future__ import annotations
from langchain_core.tools import tool
from src.main_graph.tools.search_code import _store_cache


def make_code_impact_tool(vector_store_id: str):
    @tool
    async def code_impact(package_name: str) -> list[dict]:
        """Find source files that import or use a specific npm package."""
        store = _store_cache.get(vector_store_id)
        if store is None:
            return [{"error": f"Vector store {vector_store_id} not loaded"}]
        query = f'import {package_name} require {package_name}'
        results = await store.asimilarity_search(query, k=20)
        return [
            {"file": doc.metadata.get("file", ""), "snippet": doc.page_content[:300]}
            for doc in results
            if package_name in doc.page_content
        ]

    return code_impact
