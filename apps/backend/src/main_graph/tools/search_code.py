from __future__ import annotations
import logging
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings
from src.utils.config import settings

logger = logging.getLogger(__name__)

_store_cache: dict[str, InMemoryVectorStore] = {}
_embeddings = OpenAIEmbeddings(api_key=settings.openai_api_key)

_SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".mts", ".cts"}


def get_vector_store(vector_store_id: str) -> InMemoryVectorStore | None:
    return _store_cache.get(vector_store_id)


def set_vector_store(vector_store_id: str, store: InMemoryVectorStore) -> None:
    _store_cache[vector_store_id] = store


def make_search_code_tool(vector_store_id: str):
    @tool
    async def search_code(query: str, top_k: int = 10) -> list[dict]:
        """Search repository source files for code patterns, imports, or package usage."""
        store = _store_cache.get(vector_store_id)
        if store is None:
            return [{"error": f"Vector store {vector_store_id} not loaded"}]
        results = await store.asimilarity_search(query, k=top_k)
        return [
            {"file": doc.metadata.get("file", ""), "snippet": doc.page_content[:500]}
            for doc in results
        ]

    return search_code
