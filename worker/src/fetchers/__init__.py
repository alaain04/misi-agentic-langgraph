"""Registry mapping entity_type -> FetcherEntry."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from src.rate_limiter import RateLimiter

type FetchFn = Callable[
    [httpx.AsyncClient, str, RateLimiter, int], Awaitable[dict]
]


@dataclass
class FetcherEntry:
    fetch_fn: FetchFn
    collection: str
    rate_group: str


def _build_registry() -> dict[str, FetcherEntry]:
    from src.fetchers.npm import fetch as npm_fetch
    from src.fetchers.github import (
        fetch_advisories,
        fetch_issues,
        fetch_releases,
    )
    return {
        "npm": FetcherEntry(npm_fetch, "npm_package_cache", "npm"),
        "github_issues": FetcherEntry(fetch_issues, "github_issues_cache", "github"),
        "github_releases": FetcherEntry(fetch_releases, "github_releases_cache", "github"),
        "github_advisories": FetcherEntry(fetch_advisories, "github_advisories_cache", "github"),
    }


_REGISTRY: dict[str, FetcherEntry] = {}


def _registry() -> dict[str, FetcherEntry]:
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _build_registry()
    return _REGISTRY


def get(entity_type: str) -> FetcherEntry:
    """Return FetcherEntry for the given entity type. Raises ValueError if unknown."""
    reg = _registry()
    if entity_type not in reg:
        raise ValueError(f"unknown entity type: {entity_type!r}")
    return reg[entity_type]


def entity_types() -> list[str]:
    return list(_registry().keys())


def rate_groups() -> set[str]:
    return {e.rate_group for e in _registry().values()}
