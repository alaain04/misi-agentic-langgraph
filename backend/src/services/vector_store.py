"""Per-job conversation vector store registry."""

from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings

from src.utils.config import settings

_registry: dict[str, InMemoryVectorStore] = {}


def get_or_create_store(job_id: str) -> InMemoryVectorStore:
    """Return the existing store for job_id, or create a new one."""
    if job_id not in _registry:
        embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=settings.openai_api_key,
        )
        _registry[job_id] = InMemoryVectorStore(embedding=embeddings)
    return _registry[job_id]


def delete_store(job_id: str) -> None:
    """Remove the store for job_id to free memory after job completion."""
    _registry.pop(job_id, None)
