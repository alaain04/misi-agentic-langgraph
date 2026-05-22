from src.domain.ports.vector_store_port import VectorStorePort


class LangchainVectorStoreAdapter(VectorStorePort):
    def __init__(self, store) -> None:
        self._store = store

    async def add_texts(self, texts: list[str]) -> None:
        await self._store.aadd_texts(texts)
