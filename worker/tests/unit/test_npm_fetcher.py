from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.fetchers.npm import fetch
from src.rate_limiter import TokenBucket


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


def _429(retry_after: str = "0") -> httpx.Response:
    return httpx.Response(429, headers={"Retry-After": retry_after}, json={})


async def test_fetch_returns_data_on_200():
    bucket = TokenBucket(rate=100.0)
    registry = {"name": "react", "dist-tags": {"latest": "18.0.0"}}
    downloads = {"downloads": 5_000_000}

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        side_effect=[_resp(200, registry), _resp(200, downloads)]
    )

    result = await fetch(client, "react", bucket)

    assert result["registry_data"] == registry
    assert result["weekly_downloads"] == 5_000_000


async def test_fetch_returns_empty_on_404():
    bucket = TokenBucket(rate=100.0)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=[_resp(404, {}), _resp(404, {})])

    result = await fetch(client, "nonexistent-pkg-xyz", bucket)

    assert result["registry_data"] == {}
    assert result["weekly_downloads"] is None


async def test_fetch_retries_on_429():
    bucket = TokenBucket(rate=100.0)
    registry = {"name": "lodash"}
    downloads = {"downloads": 1_000}

    registry_calls = 0

    async def dispatch_get(url, **kwargs):
        nonlocal registry_calls
        if "registry.npmjs.org" in url:
            registry_calls += 1
            if registry_calls == 1:
                return _429()
            return _resp(200, registry)
        return _resp(200, downloads)

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = dispatch_get

    with patch("src.fetchers.npm.asyncio.sleep", new_callable=AsyncMock):
        result = await fetch(client, "lodash", bucket, max_retries=2)

    assert result["registry_data"] == registry


async def test_fetch_returns_empty_after_max_retries():
    bucket = TokenBucket(rate=100.0)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_429())

    with patch("src.fetchers.npm.asyncio.sleep", new_callable=AsyncMock):
        result = await fetch(client, "react", bucket, max_retries=2)

    assert result["registry_data"] == {}
    assert result["weekly_downloads"] is None
