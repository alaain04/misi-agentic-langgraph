from __future__ import annotations

import asyncio
import dataclasses

import pytest

from src.main_graph.subgraphs.analysis.deepagent.limits import (
    DEEPAGENT_LIMITS,
    SPECIALIST_SEMAPHORE,
    DeepAgentLimits,
)


def test_default_limits():
    assert DEEPAGENT_LIMITS.max_specialist_calls == 8
    assert DEEPAGENT_LIMITS.max_parallel_calls == 3


def test_limits_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEEPAGENT_LIMITS.max_specialist_calls = 100


def test_specialist_semaphore_is_sized_to_max_parallel_calls():
    assert isinstance(SPECIALIST_SEMAPHORE, asyncio.Semaphore)
    assert SPECIALIST_SEMAPHORE._value == DeepAgentLimits().max_parallel_calls
