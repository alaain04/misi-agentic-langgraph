from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.db.input_cache import cache_key, get_or_compute, is_fresh


def test_cache_key_is_stable_and_distinct():
    k1 = cache_key("https://x/y", "sha1", "npm", "npm_audit")
    k2 = cache_key("https://x/y", "sha1", "npm", "npm_audit")
    k3 = cache_key("https://x/y", "sha1", "npm", "dependency_graph")
    assert k1 == k2
    assert k1 != k3  # kind differentiates


def test_is_fresh_within_age():
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    created = (now - timedelta(hours=1)).isoformat()
    assert is_fresh(created, max_age_seconds=7200, now=now) is True


def test_is_fresh_expired():
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    created = (now - timedelta(hours=3)).isoformat()
    assert is_fresh(created, max_age_seconds=7200, now=now) is False


def test_is_fresh_unparseable_is_not_fresh():
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    assert is_fresh("not-a-date", max_age_seconds=7200, now=now) is False


class _FakeCache:
    def __init__(self, initial: dict | None = None):
        self.store = dict(initial or {})
        self.put_calls: list[str] = []

    async def get(self, key, max_age_seconds=None):
        return self.store.get(key)

    async def put(self, key, data):
        self.put_calls.append(key)
        self.store[key] = data


@pytest.mark.asyncio
async def test_get_or_compute_hit_skips_compute():
    cache = _FakeCache({"k": {"cached": True}})
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return {"cached": False}

    result = await get_or_compute(cache, "k", compute)
    assert result == {"cached": True}
    assert calls["n"] == 0
    assert cache.put_calls == []


@pytest.mark.asyncio
async def test_get_or_compute_miss_computes_and_puts():
    cache = _FakeCache()
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return {"fresh": True}

    result = await get_or_compute(cache, "k", compute)
    assert result == {"fresh": True}
    assert calls["n"] == 1
    assert cache.put_calls == ["k"]
