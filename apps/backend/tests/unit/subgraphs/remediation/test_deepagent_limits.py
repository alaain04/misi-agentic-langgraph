from __future__ import annotations

import asyncio
import dataclasses

import pytest
from langchain_core.rate_limiters import InMemoryRateLimiter

from src.main_graph.subgraphs.remediation.deepagent.limits import (
    DEEPAGENT_LIMITS,
    MAX_RETRIES,
    REMEDIATION_RATE_LIMITER,
    TARGET_SEMAPHORE,
    DeepAgentLimits,
)


def test_default_limits():
    assert DEEPAGENT_LIMITS.max_parallel_calls == 3


def test_limits_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEEPAGENT_LIMITS.max_parallel_calls = 100


def test_target_semaphore_is_sized_to_max_parallel_calls():
    assert isinstance(TARGET_SEMAPHORE, asyncio.Semaphore)
    assert TARGET_SEMAPHORE._value == DeepAgentLimits().max_parallel_calls


def test_rate_limiter_is_a_shared_singleton_instance():
    assert isinstance(REMEDIATION_RATE_LIMITER, InMemoryRateLimiter)


def test_max_retries_is_positive():
    assert MAX_RETRIES > 0
