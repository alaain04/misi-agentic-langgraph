from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class DeepAgentLimits:
    max_specialist_calls: int = 8
    max_parallel_calls: int = 3


DEEPAGENT_LIMITS = DeepAgentLimits()
SPECIALIST_SEMAPHORE = asyncio.Semaphore(DEEPAGENT_LIMITS.max_parallel_calls)
