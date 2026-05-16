# src/redis_client.py
import logging

import redis.asyncio as aioredis

from src.config import settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None


async def connect() -> None:
    global _client
    if _client is not None:
        return
    _client = aioredis.from_url(settings.redis_url, decode_responses=False)
    await _client.ping()
    logger.info("redis: connected to %s", settings.redis_url)


async def close() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


def get_client() -> aioredis.Redis:
    if _client is None:
        raise RuntimeError("Redis not connected — call connect() first")
    return _client
