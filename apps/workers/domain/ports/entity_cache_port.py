from abc import ABC, abstractmethod


class EntityCachePort(ABC):
    @abstractmethod
    async def save(self, collection: str, name: str, doc: dict[str, object]) -> None: ...

    @abstractmethod
    async def get(self, collection: str, name: str) -> dict[str, object] | None: ...
