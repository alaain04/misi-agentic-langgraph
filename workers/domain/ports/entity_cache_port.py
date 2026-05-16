from abc import ABC, abstractmethod


class EntityCachePort(ABC):
    @abstractmethod
    async def save(self, collection: str, name: str, doc: dict) -> None: ...

    @abstractmethod
    async def get(self, collection: str, name: str) -> dict | None: ...
