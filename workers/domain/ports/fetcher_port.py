from abc import ABC, abstractmethod

import httpx


class FetcherPort(ABC):
    """Concrete classes must define `collection` and `rate_group` as class attributes."""

    collection: str
    rate_group: str

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict: ...
