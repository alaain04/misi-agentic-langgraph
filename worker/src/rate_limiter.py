import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._tokens = 1.0
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(
                    1.0, self._tokens + elapsed * self._rate
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)


class RateLimiter:
    """Rate limiter supporting multiple rate groups."""

    def __init__(self, rates: dict[str, float] | None = None) -> None:
        """Initialize with optional default rates per rate_group.

        Args:
            rates: Dict mapping rate_group name to rate (tokens per second).
                   Defaults to an empty dict; groups created on first access.
        """
        self._rates = rates or {}
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, rate_group: str) -> None:
        """Acquire a token from the specified rate_group's bucket.

        Args:
            rate_group: Name of the rate limit group (e.g., "npm", "github").
        """
        async with self._lock:
            if rate_group not in self._buckets:
                rate = self._rates.get(rate_group, 1.0)
                self._buckets[rate_group] = TokenBucket(rate=rate)

        await self._buckets[rate_group].acquire()
