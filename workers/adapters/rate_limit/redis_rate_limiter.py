"""Redis-backed multi-window sliding rate limiter using a Lua script."""

import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis

from domain.ports.rate_limit_port import RateLimitPort

logger = logging.getLogger(__name__)

_LUA = """
local now = tonumber(ARGV[1])
local req_id = ARGV[2]
for i = 1, #KEYS do
    local window_secs = tonumber(ARGV[2 + (i-1)*2 + 1])
    local max_req     = tonumber(ARGV[2 + (i-1)*2 + 2])
    redis.call('ZREMRANGEBYSCORE', KEYS[i], 0, now - window_secs)
    if redis.call('ZCARD', KEYS[i]) >= max_req then
        return 0
    end
end
for i = 1, #KEYS do
    local window_secs = tonumber(ARGV[2 + (i-1)*2 + 1])
    redis.call('ZADD', KEYS[i], now, req_id)
    redis.call('EXPIRE', KEYS[i], window_secs + 1)
end
return 1
"""


class RedisRateLimiter(RateLimitPort):
    def __init__(
        self, redis_url: str, windows: dict[str, list[tuple[int, int]]]
    ) -> None:
        """
        windows: maps rate_group -> list of (window_seconds, max_requests).
        Example: {"npm": [(60, 500), (3600, 5000)]}
        """
        self._client: aioredis.Redis = aioredis.from_url(
            redis_url, decode_responses=False
        )
        self._windows = windows
        self._sha: str | None = None
        self._load_lock = asyncio.Lock()

    async def _ensure_loaded(self) -> None:
        async with self._load_lock:
            if self._sha is None:
                self._sha = await self._client.script_load(_LUA)

    async def acquire(self, group: str) -> None:
        """Block until a request slot is available for group."""
        windows = self._windows[group]  # KeyError for unknown group
        await self._ensure_loaded()
        keys = [f"ratelimit:{group}:{w}" for w, _ in windows]
        argv_pairs = [str(v) for w, m in windows for v in (w, m)]

        while True:
            now = time.time()
            req_id = str(uuid.uuid4())
            result = await self._client.evalsha(
                self._sha, len(keys), *keys, str(now), req_id, *argv_pairs
            )
            if result == 1:
                return

            wait = 1.0
            for key, (window_secs, _) in zip(keys, windows, strict=True):
                oldest = await self._client.zrange(key, 0, 0, withscores=True)
                if oldest:
                    _, score = oldest[0]
                    slot_open = score + window_secs - now
                    wait = max(wait, slot_open)

            logger.debug("rate limiter: %s throttled, waiting %.1fs", group, wait)
            await asyncio.sleep(wait)
