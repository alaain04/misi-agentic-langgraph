from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.fetchers.npm_fetcher_adapter import NpmFetcherAdapter
from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.rate_limit_port import RateLimitPort


def _make_fetcher() -> NpmFetcherAdapter:
    mock_limiter = MagicMock(spec=RateLimitPort)
    mock_limiter.acquire = AsyncMock()
    return NpmFetcherAdapter(mock_limiter, max_retries=3)


def _resp(status: int, body: dict | None = None, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body or {})
    r.headers = headers or {}
    return r


async def test_fetch_returns_doc_on_200():
    fetcher = _make_fetcher()
    reg = _resp(200, {"name": "react"})
    dl = _resp(200, {"downloads": 1000})
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[reg, dl])
    doc = await fetcher.fetch(client, "react")
    assert doc["registry_data"] == {"name": "react"}
    assert doc["weekly_downloads"] == 1000


async def test_fetch_raises_rate_limit_on_429():
    fetcher = _make_fetcher()
    r = _resp(429, headers={"Retry-After": "60"})
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    with pytest.raises(RateLimitError) as exc:
        await fetcher.fetch(client, "react")
    assert exc.value.delay == 60.0


async def test_fetch_raises_permanent_on_404():
    fetcher = _make_fetcher()
    r = _resp(404)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    with pytest.raises(PermanentFetchError):
        await fetcher.fetch(client, "no-such-pkg")


async def test_fetch_raises_transient_after_500_exhaustion():
    fetcher = _make_fetcher()
    r = _resp(500)
    client = AsyncMock()
    client.get = AsyncMock(return_value=r)
    with patch("adapters.fetchers.npm_fetcher_adapter.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TransientFetchError):
            await fetcher.fetch(client, "react")
    assert client.get.call_count == 6  # 3 retries × 2 parallel calls


async def test_collection_and_rate_group():
    fetcher = _make_fetcher()
    assert fetcher.collection == "npm_package_cache"
    assert fetcher.rate_group == "npm"
