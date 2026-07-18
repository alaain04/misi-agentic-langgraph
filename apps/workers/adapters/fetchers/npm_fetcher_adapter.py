"""npm registry metadata fetcher."""

import asyncio
import logging
import urllib.parse

import httpx

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.fetcher_port import FetcherPort
from domain.ports.rate_limit_port import RateLimitPort

logger = logging.getLogger(__name__)

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"
_TIMEOUT = 15.0


class NpmFetcherAdapter(FetcherPort):
    collection = "npm_package_cache"
    rate_group = "npm"

    def __init__(self, rate_limiter: RateLimitPort, max_retries: int = 3) -> None:
        self._rate_limiter = rate_limiter
        self._max_retries = max_retries

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict[str, object]:
        encoded = urllib.parse.quote(name, safe="")
        reg_resp, dl_resp = await asyncio.gather(
            self._get(client, f"{_NPM_REGISTRY}/{encoded}"),
            self._get(client, f"{_NPM_DOWNLOADS}/{encoded}"),
        )
        return {
            "registry_data": reg_resp.json(),
            "weekly_downloads": dl_resp.json().get("downloads"),
        }

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        for attempt in range(self._max_retries):
            await self._rate_limiter.acquire("npm")
            try:
                resp = await client.get(url, timeout=_TIMEOUT)
            except httpx.RequestError as exc:
                if attempt == self._max_retries - 1:
                    raise TransientFetchError(str(exc)) from exc
                await asyncio.sleep(2**attempt)
                continue

            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                delay = float(resp.headers.get("Retry-After", 60))
                raise RateLimitError(delay)
            if resp.status_code == 404:
                raise PermanentFetchError(f"404 for {url}")
            if resp.status_code >= 500:
                if attempt == self._max_retries - 1:
                    raise TransientFetchError(f"status {resp.status_code} for {url}")
                await asyncio.sleep(2**attempt)
                continue
            raise PermanentFetchError(f"unexpected status {resp.status_code} for {url}")

        raise TransientFetchError(f"exhausted {self._max_retries} retries for {url}")
