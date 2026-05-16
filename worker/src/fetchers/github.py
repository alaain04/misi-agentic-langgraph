"""GitHub REST API fetchers: issues, releases, security advisories."""

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx

from src.config import settings
from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"
_TIMEOUT = 20.0


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _next_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None


async def _get_pages(
    client: httpx.AsyncClient,
    url: str,
    rate_limiter: RateLimiter,
    max_retries: int,
) -> list[dict]:
    """Fetch all pages from a paginated GitHub endpoint."""
    results: list[dict] = []
    next_url: str | None = url

    while next_url:
        for attempt in range(max_retries):
            await rate_limiter.acquire("github")
            try:
                resp = await client.get(next_url, headers=_auth_headers(), timeout=_TIMEOUT)
            except httpx.RequestError as exc:
                if attempt == max_retries - 1:
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
                if attempt == max_retries - 1:
                    raise TransientFetchError(f"status {resp.status_code} for {next_url}")
                continue
            raise PermanentFetchError(f"unexpected status {resp.status_code} for {next_url}")
        else:
            raise TransientFetchError(f"exhausted retries for {next_url}")

    return results


async def fetch_issues(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Fetch issues created/updated in the last github_issues_lookback_days days."""
    since = (
        datetime.now(UTC) - timedelta(days=settings.github_issues_lookback_days)
    ).isoformat()
    url = f"{_BASE}/repos/{name}/issues?state=all&since={since}&per_page=100"
    issues = await _get_pages(client, url, rate_limiter, max_retries)
    return {"issues": issues}


async def fetch_releases(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Fetch releases published in the last github_releases_lookback_days days."""
    url = f"{_BASE}/repos/{name}/releases?per_page=100"
    all_releases = await _get_pages(client, url, rate_limiter, max_retries)
    cutoff = datetime.now(UTC) - timedelta(days=settings.github_releases_lookback_days)
    recent = [
        r for r in all_releases
        if r.get("published_at")
        and datetime.fromisoformat(r["published_at"]) >= cutoff
    ]
    return {"releases": recent}


async def fetch_advisories(
    client: httpx.AsyncClient,
    name: str,
    rate_limiter: RateLimiter,
    max_retries: int = 3,
) -> dict:
    """Fetch all repository security advisories."""
    url = f"{_BASE}/repos/{name}/security-advisories?per_page=100"
    advisories = await _get_pages(client, url, rate_limiter, max_retries)
    return {"advisories": advisories}
