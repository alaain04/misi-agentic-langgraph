"""GitHub REST API fetchers: issues, releases, security advisories."""

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx

from domain.models.errors import PermanentFetchError, RateLimitError, TransientFetchError
from domain.ports.fetcher_port import FetcherPort
from domain.ports.rate_limit_port import RateLimitPort

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"
_TIMEOUT = 20.0


def _next_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None


class _GithubBaseFetcher(FetcherPort):
    rate_group = "github"

    def __init__(
        self, rate_limiter: RateLimitPort, token: str, max_retries: int = 3
    ) -> None:
        self._rate_limiter = rate_limiter
        self._token = token
        self._max_retries = max_retries

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get_pages(self, client: httpx.AsyncClient, url: str) -> list[dict]:
        results: list[dict] = []
        next_url: str | None = url
        while next_url:
            for attempt in range(self._max_retries):
                await self._rate_limiter.acquire("github")
                try:
                    resp = await client.get(
                        next_url, headers=self._auth_headers(), timeout=_TIMEOUT
                    )
                except httpx.RequestError as exc:
                    if attempt == self._max_retries - 1:
                        raise TransientFetchError(str(exc)) from exc
                    continue

                if resp.status_code == 200:
                    page = resp.json()
                    results.extend(page if isinstance(page, list) else [page])
                    next_url = _next_url(resp.headers.get("link"))
                    break
                if resp.status_code == 429:
                    delay = float(resp.headers.get("Retry-After", 60))
                    raise RateLimitError(delay)
                if resp.status_code == 404:
                    raise PermanentFetchError(f"404 for {next_url}")
                if resp.status_code >= 500:
                    if attempt == self._max_retries - 1:
                        raise TransientFetchError(
                            f"status {resp.status_code} for {next_url}"
                        )
                    continue
                raise PermanentFetchError(
                    f"unexpected status {resp.status_code} for {next_url}"
                )
        return results


class GithubIssuesFetcherAdapter(_GithubBaseFetcher):
    collection = "github_issues_cache"

    def __init__(
        self,
        rate_limiter: RateLimitPort,
        token: str,
        lookback_days: int = 30,
        max_retries: int = 3,
    ) -> None:
        super().__init__(rate_limiter, token, max_retries)
        self._lookback_days = lookback_days

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict:
        since = (
            datetime.now(UTC) - timedelta(days=self._lookback_days)
        ).isoformat()
        url = f"{_BASE}/repos/{name}/issues?state=all&since={since}&per_page=100"
        issues = await self._get_pages(client, url)
        return {"issues": issues}


class GithubReleasesFetcherAdapter(_GithubBaseFetcher):
    collection = "github_releases_cache"

    def __init__(
        self,
        rate_limiter: RateLimitPort,
        token: str,
        lookback_days: int = 90,
        max_retries: int = 3,
    ) -> None:
        super().__init__(rate_limiter, token, max_retries)
        self._lookback_days = lookback_days

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict:
        url = f"{_BASE}/repos/{name}/releases?per_page=100"
        all_releases = await self._get_pages(client, url)
        cutoff = datetime.now(UTC) - timedelta(days=self._lookback_days)
        recent = [
            r
            for r in all_releases
            if r.get("published_at")
            and datetime.fromisoformat(r["published_at"]) >= cutoff
        ]
        return {"releases": recent}


class GithubAdvisoriesFetcherAdapter(_GithubBaseFetcher):
    collection = "github_advisories_cache"

    async def fetch(self, client: httpx.AsyncClient, name: str) -> dict:
        url = f"{_BASE}/repos/{name}/security-advisories?per_page=100"
        advisories = await self._get_pages(client, url)
        return {"advisories": advisories}
