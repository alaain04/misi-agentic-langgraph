import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from src.fetchers.errors import PermanentFetchError, RateLimitError, TransientFetchError
from src.fetchers.github import fetch_advisories, fetch_issues, fetch_releases
from src.rate_limiter import RateLimiter


def _make_limiter() -> RateLimiter:
    limiter = MagicMock(spec=RateLimiter)
    limiter.acquire = AsyncMock()
    return limiter


def _resp(status: int, body=None, headers: dict | None = None, link: str | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body if body is not None else [])
    h = dict(headers or {})
    if link:
        h["link"] = link
    r.headers = h
    return r


@pytest.mark.asyncio
async def test_fetch_issues_returns_list():
    issue = {"id": 1, "title": "bug"}
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [issue]))
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(
        github_token="tok",
        github_issues_lookback_days=30,
    )
    doc = await fetch_issues(client, "owner/repo", limiter, max_retries=3)
    gh_module.settings = original

    assert doc["issues"] == [issue]


@pytest.mark.asyncio
async def test_fetch_issues_raises_rate_limit_on_429():
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(429, headers={"Retry-After": "30"}))
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(
        github_token="tok",
        github_issues_lookback_days=30,
    )
    with pytest.raises(RateLimitError) as exc_info:
        await fetch_issues(client, "owner/repo", limiter, max_retries=1)
    gh_module.settings = original
    assert exc_info.value.delay == 30.0


@pytest.mark.asyncio
async def test_fetch_releases_filters_by_date():
    now = datetime.now(UTC)
    old = (now - timedelta(days=100)).isoformat()
    recent = (now - timedelta(days=5)).isoformat()
    releases = [
        {"id": 1, "published_at": recent},
        {"id": 2, "published_at": old},
    ]
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, releases))
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(
        github_token="tok",
        github_releases_lookback_days=30,
    )
    doc = await fetch_releases(client, "owner/repo", limiter, max_retries=3)
    gh_module.settings = original

    assert len(doc["releases"]) == 1
    assert doc["releases"][0]["id"] == 1


@pytest.mark.asyncio
async def test_fetch_advisories_returns_list():
    advisory = {"ghsa_id": "GHSA-1234", "severity": "high"}
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [advisory]))
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(github_token="tok")
    doc = await fetch_advisories(client, "owner/repo", limiter, max_retries=3)
    gh_module.settings = original

    assert doc["advisories"] == [advisory]


@pytest.mark.asyncio
async def test_fetch_issues_paginates():
    page1 = [{"id": 1}]
    page2 = [{"id": 2}]
    link_header = '<https://api.github.com/next>; rel="next"'
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[
        _resp(200, page1, link=link_header),
        _resp(200, page2),
    ])
    limiter = _make_limiter()

    import src.fetchers.github as gh_module
    original = gh_module.settings
    gh_module.settings = MagicMock(
        github_token="tok",
        github_issues_lookback_days=30,
    )
    doc = await fetch_issues(client, "owner/repo", limiter, max_retries=3)
    gh_module.settings = original

    assert len(doc["issues"]) == 2
