"""Commit-SHA-keyed cache for deterministic, expensive pipeline inputs.

A cache HIT only ever happens when the exact same (repo_url, commit_sha,
package_manager, kind) is analyzed again — i.e. re-runs of the same commit
(the determinism/fixture-corpus testing workflow). The cache is strictly an
optimization: callers must fall back to recompute on miss or error.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from src.db.connection import get_db

logger = logging.getLogger(__name__)


def cache_key(repo_url: str, commit_sha: str, pm: str, kind: str) -> str:
    return f"{repo_url}@{commit_sha}:{pm}:{kind}"


def is_fresh(created_at_iso: str, max_age_seconds: float, now: datetime) -> bool:
    """True if the entry's age is within max_age_seconds. Unparseable
    timestamps are treated as not fresh (force recompute)."""
    try:
        created = datetime.fromisoformat(created_at_iso)
    except (ValueError, TypeError):
        return False
    return (now - created).total_seconds() <= max_age_seconds


async def get_or_compute(
    cache: InputCacheDAO,
    key: str,
    compute: Callable[[], Awaitable[dict]],
    max_age_seconds: float | None = None,
) -> dict:
    """Return the cached value for key, else compute it, store it, and return.

    Never raises on cache failure — a cache error degrades to a plain compute.
    """
    try:
        cached = await cache.get(key, max_age_seconds)
    except Exception as exc:
        logger.warning("input_cache: get failed for %s: %s", key, exc)
        cached = None
    if cached is not None:
        return cached

    value = await compute()

    try:
        await cache.put(key, value)
    except Exception as exc:
        logger.warning("input_cache: put failed for %s: %s", key, exc)
    return value


class InputCacheDAO:
    def __init__(self) -> None:
        self._col = get_db()["input_cache"]

    async def get(self, key: str, max_age_seconds: float | None = None) -> dict | None:
        doc = await self._col.find_one({"key": key}, {"_id": 0})
        if doc is None:
            return None
        if max_age_seconds is not None and not is_fresh(
            doc.get("created_at", ""), max_age_seconds, datetime.now(UTC)
        ):
            return None
        data = doc.get("data")
        return data if isinstance(data, dict) else None

    async def put(self, key: str, data: dict) -> None:
        await self._col.update_one(
            {"key": key},
            {
                "$set": {
                    "key": key,
                    "data": data,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            },
            upsert=True,
        )
