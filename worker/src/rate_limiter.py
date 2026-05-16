# src/rate_limiter.py
"""Redis-backed multi-window sliding rate limiter."""

import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis

from src.redis_client import get_client

logger = logging.getLogger(__name__)

# Lua script: atomically check all windows, consume if all have capacity.
# KEYS: one key per window  e.g. ["ratelimit:npm:60", "ratelimit:npm:3600"]
# ARGV: now, req_id, then pairs of window_secs,max_req per key
# Returns 1 if consumed, 0 if any window is full.
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


class RateLimiter:
    def __init__(self, windows: dict[str, list[tuple[int, int]]]) -> None:
        """
        windows: maps rate_group -> list of (window_seconds, max_requests).
        Example: {"npm": [(60, 500), (3600, 5000)]}
        """
        self._windows = windows
        self._redis: aioredis.Redis | None = None
        self._sha: str | None = None

    async def _ensure_loaded(self) -> None:
        if self._sha is None:
            self._redis = get_client()
            self._sha = await self._redis.script_load(_LUA)

    async def acquire(self, rate_group: str) -> None:
        """Block until a request slot is available for rate_group."""
        windows = self._windows[rate_group]  # raises KeyError for unknown group
        await self._ensure_loaded()
        keys = [f"ratelimit:{rate_group}:{w}" for w, _ in windows]
        argv_pairs = [str(v) for w, m in windows for v in (w, m)]

        while True:
            now = time.time()
            req_id = str(uuid.uuid4())
            result = await self._redis.evalsha(
                self._sha, len(keys), *keys, str(now), req_id, *argv_pairs
            )
            if result == 1:
                return

            # Find earliest slot opening across all saturated windows
            wait = 1.0
            for key, (window_secs, _) in zip(keys, windows):
                oldest = await self._redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    _, score = oldest[0]
                    slot_open = score + window_secs - now
                    wait = max(wait, slot_open)

            logger.debug("rate limiter: %s throttled, waiting %.1fs", rate_group, wait)
            await asyncio.sleep(wait)
