from abc import ABC, abstractmethod
from typing import Any


class CachePort(ABC):
    """Defines the contract for cache implementations."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Retrieve a value by key. Returns None if not found."""
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value with an optional TTL in seconds."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a value by key."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check whether a key exists in the cache."""
        ...

    @abstractmethod
    async def flush(self) -> None:
        """Clear all entries from the cache."""
        ...
