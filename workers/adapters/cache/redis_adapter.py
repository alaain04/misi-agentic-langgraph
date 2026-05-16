import json
from typing import Any

import redis.asyncio as aioredis

from domain.ports.cache_port import CachePort


class RedisCacheAdapter(CachePort):
    """Redis implementation of the CachePort.

    Connects to Redis using the URL from settings and serialises values as JSON.
    """

    def __init__(self, redis_url: str) -> None:
        self._client: aioredis.Redis[str] = aioredis.from_url(
            redis_url, encoding="utf-8", decode_responses=True
        )

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        serialised = json.dumps(value)
        if ttl is not None:
            await self._client.setex(key, ttl, serialised)
        else:
            await self._client.set(key, serialised)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def exists(self, key: str) -> bool:
        return bool(await self._client.exists(key))

    async def flush(self) -> None:
        await self._client.flushdb()

    async def close(self) -> None:
        await self._client.close()
