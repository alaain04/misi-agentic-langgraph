"""Concurrency and rate-limit guards for the remediation deep agent.

deepagents' own `task` tool prompt tells the coordinating LLM to dispatch
multiple targets in one turn ("launch multiple agents concurrently whenever
possible"), and langgraph's ToolNode runs every tool call from that turn via
`asyncio.gather` with no cap of its own. Without a guard here, N open
targets means N concurrent nested deep agents -- each making several
sequential LLM calls of its own -- all racing against the same OpenAI
account's rate limit. TARGET_SEMAPHORE bounds how many nested agents run at
once (mirrors analysis/deepagent/limits.py's SPECIALIST_SEMAPHORE);
REMEDIATION_RATE_LIMITER additionally smooths the request rate within that
budget. Both must stay shared singletons -- a limiter or semaphore
constructed fresh per call (e.g. inside get_llm()) would give each nested
agent its own independent budget and throttle nothing in aggregate.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from langchain_core.rate_limiters import InMemoryRateLimiter


@dataclass(frozen=True)
class DeepAgentLimits:
    max_parallel_calls: int = 3


DEEPAGENT_LIMITS = DeepAgentLimits()
TARGET_SEMAPHORE = asyncio.Semaphore(DEEPAGENT_LIMITS.max_parallel_calls)

REMEDIATION_RATE_LIMITER = InMemoryRateLimiter(
    requests_per_second=2,
    check_every_n_seconds=0.1,
    max_bucket_size=4,
)

MAX_RETRIES = 6
