"""Registry mapping entity_type → (fetch_fn, cache_collection)."""

from collections.abc import Callable

import httpx

from src.fetchers.npm import fetch as npm_fetch
from src.rate_limiter import TokenBucket

type FetchFn = Callable[
    [httpx.AsyncClient, str, TokenBucket, int], object
]

_REGISTRY: dict[str, tuple[FetchFn, str]] = {
    "npm": (npm_fetch, "npm_package_cache"),
}


def get(entity_type: str) -> tuple[FetchFn, str]:
    """Return (fetch_fn, cache_collection) for the given entity type."""
    if entity_type not in _REGISTRY:
        raise ValueError(f"unknown entity type: {entity_type!r}")
    return _REGISTRY[entity_type]
