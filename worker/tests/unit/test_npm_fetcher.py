import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.fetchers.npm import fetch
from src.rate_limiter import TokenBucket


def _make_limiter() -> TokenBucket:
    limiter = MagicMock(spec=TokenBucket)
    limiter.acquire = AsyncMock()
    return limiter


def _resp(status: int, body: dict | None = None, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body or {})
    r.headers = headers or {}
    return r


@pytest.mark.asyncio
async def test_fetch_returns_doc_on_200():
    reg = _resp(200, {"name": "react"})
    dl = _resp(200, {"downloads": 1000})
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[reg, dl])
    limiter = _make_limiter()

    doc = await fetch(client, "react", limiter, max_retries=3)

    assert doc["registry_data"] == {"name": "react"}
    assert doc["weekly_downloads"] == 1000


@pytest.mark.asyncio
async def test_fetch_raises_rate_limit_error_on_429():
    r = _resp(429, headers={"Retry-After": "60"})
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    limiter = _make_limiter()

    with pytest.raises(RateLimitError) as exc_info:
        await fetch(client, "react", limiter, max_retries=1)
    assert exc_info.value.delay == 60.0


@pytest.mark.asyncio
async def test_fetch_raises_permanent_error_on_404():
    r = _resp(404)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    limiter = _make_limiter()

    with pytest.raises(PermanentFetchError):
        await fetch(client, "no-such-package", limiter, max_retries=3)


@pytest.mark.asyncio
async def test_fetch_raises_transient_error_on_500():
    r = _resp(500)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    limiter = _make_limiter()

    with pytest.raises(TransientFetchError):
        await fetch(client, "react", limiter, max_retries=3)
