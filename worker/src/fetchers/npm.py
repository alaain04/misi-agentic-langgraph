"""Fetch npm registry metadata and weekly downloads."""

import asyncio
import logging
import urllib.parse

import httpx

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
_TIMEOUT = 15.0


def _encode(name: str) -> str:
    return urllib.parse.quote(name, safe="")


async def _get(
    client: httpx.AsyncClient,
    url: str,
    rate_limiter: RateLimiter,
    max_retries: int,
) -> httpx.Response:
    """Fetch one URL, acquiring a rate-limit slot first. Raises on non-200."""
    for attempt in range(max_retries):
        await rate_limiter.acquire("npm")
        try:
            resp = await client.get(url, timeout=_TIMEOUT)
        except httpx.RequestError as exc:
            if attempt == max_retries - 1:
                raise TransientFetchError(str(exc)) from exc
            await asyncio.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            return resp
        if resp.status_code == 429:
            delay = float(resp.headers.get("Retry-After", 60))
            raise RateLimitError(delay)
        if resp.status_code == 404:
            raise PermanentFetchError(f"404 for {url}")
        if resp.status_code >= 500:
            if attempt == max_retries - 1:
                raise TransientFetchError(f"status {resp.status_code} for {url}")
            await asyncio.sleep(2 ** attempt)
            continue
        raise PermanentFetchError(f"unexpected status {resp.status_code} for {url}")

    raise TransientFetchError(f"exhausted {max_retries} retries for {url}")


async def fetch(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Return cache document dict for one npm package."""
    encoded = _encode(name)
    reg_resp, dl_resp = await asyncio.gather(
        _get(client, f"{_NPM_REGISTRY}/{encoded}", rate_limiter, max_retries),
        _get(client, f"{_NPM_DOWNLOADS}/{encoded}", rate_limiter, max_retries),
    )
    return {
        "registry_data": reg_resp.json(),
        "weekly_downloads": dl_resp.json().get("downloads"),
    }
