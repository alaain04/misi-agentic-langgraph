from abc import ABC, abstractmethod
from typing import Any


class IngestionResultPort(ABC):
    @abstractmethod
    async def save(self, entry: Any) -> str: ...

    @abstractmethod
    async def get(self, doc_id: str) -> dict | None: ...
