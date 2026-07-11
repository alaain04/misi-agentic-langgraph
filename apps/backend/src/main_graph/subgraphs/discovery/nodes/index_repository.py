from __future__ import annotations
import logging
import os
import uuid
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.main_graph.tools.search_code import set_vector_store, _SOURCE_EXTENSIONS
from src.utils.config import settings

logger = logging.getLogger(__name__)

_embeddings = OpenAIEmbeddings(api_key=settings.openai_api_key)
_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

_MAX_FILES = 200
_MAX_FILE_BYTES = 50_000


def _walk_source_files(repo_path: str) -> list[tuple[str, str]]:
    """Return list of (relative_path, content) for source files."""
    results = []
    skip_dirs = {"node_modules", ".git", "dist", "build", ".next", "coverage"}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if os.path.splitext(fname)[1] not in _SOURCE_EXTENSIONS:
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


async def index_repository(state: dict) -> dict:
    repo_path = state.get("repo_path", "")
    if not repo_path or not os.path.isdir(repo_path):
        logger.warning("index_repository: repo_path missing or not a dir, skipping")
        return {"vector_store_id": ""}

    files = _walk_source_files(repo_path)
    logger.info("index_repository: indexing %d source files", len(files))

    docs: list[Document] = []
    for rel_path, content in files:
        chunks = _splitter.split_text(content)
        for chunk in chunks:
            docs.append(Document(page_content=chunk, metadata={"file": rel_path}))

    vector_store_id = str(uuid.uuid4())
    store = InMemoryVectorStore(embedding=_embeddings)
    if docs:
        await store.aadd_documents(docs)

    set_vector_store(vector_store_id, store)
    logger.info("index_repository: vector_store_id=%s docs=%d", vector_store_id, len(docs))
    return {"vector_store_id": vector_store_id}
