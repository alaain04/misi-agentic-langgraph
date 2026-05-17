import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.rate_limit.redis_rate_limiter import RedisRateLimiter


def _make_limiter(windows: dict) -> tuple[RedisRateLimiter, MagicMock]:
    limiter = RedisRateLimiter("redis://unused", windows)
    mock_redis = MagicMock()
    mock_redis.evalsha = AsyncMock()
    mock_redis.script_load = AsyncMock(return_value="faksha")
    mock_redis.zrange = AsyncMock(return_value=[])
    limiter._client = mock_redis
    limiter._sha = "faksha"
    return limiter, mock_redis


async def test_acquire_succeeds_when_slot_available():
    limiter, mock_redis = _make_limiter({"npm": [(60, 100)]})
    mock_redis.evalsha = AsyncMock(return_value=1)
    await limiter.acquire("npm")
    mock_redis.evalsha.assert_called_once()


async def test_acquire_retries_when_no_slot():
    limiter, mock_redis = _make_limiter({"npm": [(60, 100)]})
    mock_redis.evalsha = AsyncMock(side_effect=[0, 1])
    mock_redis.zrange = AsyncMock(return_value=[(b"id", time.time() + 0.05)])

    with patch(
        "adapters.rate_limit.redis_rate_limiter.asyncio.sleep", new_callable=AsyncMock
    ) as mock_sleep:
        await limiter.acquire("npm")
        mock_sleep.assert_called_once()


async def test_acquire_raises_for_unknown_group():
    limiter, _ = _make_limiter({"npm": [(60, 100)]})
    with pytest.raises(KeyError):
        await limiter.acquire("github")
