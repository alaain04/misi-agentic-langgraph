from abc import ABC, abstractmethod


class RateLimitPort(ABC):
    @abstractmethod
    async def acquire(self, group: str) -> None: ...
