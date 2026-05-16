import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.rate_limiter import RateLimiter


def _make_limiter(windows: dict) -> RateLimiter:
    mock_redis = MagicMock()
    mock_redis.evalsha = AsyncMock()
    mock_redis.script_load = AsyncMock(return_value="faksha")
    limiter = RateLimiter(windows)
    limiter._redis = mock_redis
    limiter._sha = "faksha"
    return limiter


@pytest.mark.asyncio
async def test_acquire_succeeds_when_slot_available():
    limiter = _make_limiter({"npm": [(60, 100)]})
    limiter._redis.evalsha = AsyncMock(return_value=1)
    await limiter.acquire("npm")
    limiter._redis.evalsha.assert_called_once()


@pytest.mark.asyncio
async def test_acquire_retries_when_no_slot():
    limiter = _make_limiter({"npm": [(60, 100)]})
    # First call: rejected (0), second call: accepted (1)
    limiter._redis.evalsha = AsyncMock(side_effect=[0, 1])
    limiter._redis.zrange = AsyncMock(return_value=[(b"id", time.time() + 0.05)])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.acquire("npm")
        mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_acquire_raises_for_unknown_group():
    limiter = _make_limiter({"npm": [(60, 100)]})
    with pytest.raises(KeyError):
        await limiter.acquire("github")
