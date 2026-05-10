import asyncio
import time

import pytest

from src.rate_limiter import TokenBucket


async def test_single_acquire_does_not_block():
    bucket = TokenBucket(rate=10.0)
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


async def test_rate_is_applied():
    bucket = TokenBucket(rate=5.0)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    # 5 tokens at 5/s: first is free, next 4 cost 0.2s each → ~0.8s
    assert elapsed >= 0.7


async def test_tokens_refill_over_time():
    bucket = TokenBucket(rate=10.0)
    # drain all tokens
    for _ in range(10):
        await bucket.acquire()
    # wait for refill
    await asyncio.sleep(0.5)
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1
