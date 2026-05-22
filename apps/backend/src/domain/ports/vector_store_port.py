from abc import ABC, abstractmethod


class VectorStorePort(ABC):
    @abstractmethod
    async def add_texts(self, texts: list[str]) -> None: ...
