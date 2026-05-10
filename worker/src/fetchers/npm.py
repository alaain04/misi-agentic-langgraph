"""Fetch npm registry metadata and weekly downloads with retry on 429."""

import asyncio
import logging
import urllib.parse

import httpx

from src.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
_TIMEOUT = 15.0


def _encode(name: str) -> str:
    return urllib.parse.quote(name, safe="")


async def _get(
    client: httpx.AsyncClient,
    url: str,
    bucket: TokenBucket,
    max_retries: int,
) -> httpx.Response | None:
    backoff = 1.0
    for _ in range(max_retries):
        await bucket.acquire()
        try:
            resp = await client.get(url, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", backoff))
                logger.debug("npm: 429 on %s, retry in %.1fs", url, wait)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 60.0)
                continue
            logger.warning("npm: unexpected status %d for %s", resp.status_code, url)
            return None
        except httpx.RequestError as exc:
            logger.debug("npm: request error %s: %s", url, exc)
            await asyncio.sleep(backoff)
            backoff *= 2
    return None


async def fetch(
    client: httpx.AsyncClient,
    name: str,
    bucket: TokenBucket,
    max_retries: int = 3,
) -> dict:
    """Return cache document dict for one npm package."""
    encoded = _encode(name)
    reg_resp, dl_resp = await asyncio.gather(
        _get(client, f"{_NPM_REGISTRY}/{encoded}", bucket, max_retries),
        _get(client, f"{_NPM_DOWNLOADS}/{encoded}", bucket, max_retries),
    )
    return {
        "registry_data": reg_resp.json() if reg_resp else {},
        "weekly_downloads": (
            dl_resp.json().get("downloads") if dl_resp else None
        ),
    }
