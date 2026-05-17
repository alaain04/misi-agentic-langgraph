from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MessagingPort(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def publish(
        self,
        topic: str,
        message: dict[str, Any],
        attributes: dict[str, str] | None = None,
    ) -> str: ...

    @abstractmethod
    async def subscribe(self, queue: str, handler: MessageHandler) -> None: ...

    @abstractmethod
    async def acknowledge(self, receipt_handle: str) -> None: ...

    # --- JetStream methods ---

    @abstractmethod
    async def add_stream(self, name: str, subjects: list[str]) -> None: ...

    @abstractmethod
    async def pull_subscribe(
        self, stream: str, subject: str, durable: str, max_deliver: int
    ) -> Any: ...

    @abstractmethod
    async def pull_fetch(self, subscription: Any, batch: int, timeout: float) -> list[Any]: ...

    @abstractmethod
    async def ack(self, msg: Any) -> None: ...

    @abstractmethod
    async def nak(self, msg: Any, delay: float) -> None: ...

    @abstractmethod
    async def term(self, msg: Any) -> None: ...
