from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.fetchers.github_fetcher_adapter import (
    GithubAdvisoriesFetcherAdapter,
    GithubIssuesFetcherAdapter,
    GithubReleasesFetcherAdapter,
)
from domain.models.errors import RateLimitError
from domain.ports.rate_limit_port import RateLimitPort


def _make_limiter() -> MagicMock:
    m = MagicMock(spec=RateLimitPort)
    m.acquire = AsyncMock()
    return m


def _resp(status: int, body=None, link: str | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body if body is not None else [])
    h: dict = {}
    if link:
        h["link"] = link
    r.headers = h
    return r


async def test_fetch_issues_returns_list():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30)
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [{"id": 1}]))
    doc = await fetcher.fetch(client, "owner/repo")
    assert doc["issues"] == [{"id": 1}]


async def test_fetch_issues_paginates():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30)
    link = '<https://api.github.com/next>; rel="next"'
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[_resp(200, [{"id": 1}], link=link), _resp(200, [{"id": 2}])]
    )
    doc = await fetcher.fetch(client, "owner/repo")
    assert len(doc["issues"]) == 2


async def test_fetch_issues_raises_rate_limit_on_429():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30, max_retries=1)
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(429))
    with pytest.raises(RateLimitError) as exc:
        await fetcher.fetch(client, "owner/repo")
    assert exc.value.delay == 60.0


async def test_fetch_releases_filters_by_lookback():
    fetcher = GithubReleasesFetcherAdapter(_make_limiter(), token="tok", lookback_days=30)
    now = datetime.now(UTC)
    old = (now - timedelta(days=100)).isoformat()
    recent = (now - timedelta(days=5)).isoformat()
    client = AsyncMock()
    client.get = AsyncMock(
        return_value=_resp(200, [{"id": 1, "published_at": recent}, {"id": 2, "published_at": old}])
    )
    doc = await fetcher.fetch(client, "owner/repo")
    assert len(doc["releases"]) == 1
    assert doc["releases"][0]["id"] == 1


async def test_fetch_advisories_returns_list():
    fetcher = GithubAdvisoriesFetcherAdapter(_make_limiter(), token="tok")
    client = AsyncMock()
    client.get = AsyncMock(return_value=_resp(200, [{"ghsa_id": "GHSA-1234"}]))
    doc = await fetcher.fetch(client, "owner/repo")
    assert doc["advisories"] == [{"ghsa_id": "GHSA-1234"}]


async def test_collection_and_rate_group_issues():
    fetcher = GithubIssuesFetcherAdapter(_make_limiter(), token="", lookback_days=30)
    assert fetcher.collection == "github_issues_cache"
    assert fetcher.rate_group == "github"


async def test_collection_and_rate_group_releases():
    fetcher = GithubReleasesFetcherAdapter(_make_limiter(), token="", lookback_days=90)
    assert fetcher.collection == "github_releases_cache"
    assert fetcher.rate_group == "github"


async def test_collection_and_rate_group_advisories():
    fetcher = GithubAdvisoriesFetcherAdapter(_make_limiter(), token="")
    assert fetcher.collection == "github_advisories_cache"
    assert fetcher.rate_group == "github"
