"""Concurrency and rate-limit guards for the remediation deep agent.

remediate_targets_node (nodes/remediate_targets.py) runs one flat execution agent per
connected group of targets, fanned out across independent groups via
asyncio.TaskGroup. TARGET_SEMAPHORE bounds how many of those group agents
run at once -- previously it bounded nested per-target agents dispatched via
deepagents' own `task()` tool (spec 2026-08-08-remediation-flatten-planning-
execution retired that dispatch path entirely, so there is no longer an
unbounded framework-driven fan-out to guard against; the semaphore now just
caps the concurrency Python itself creates). REMEDIATION_RATE_LIMITER
additionally smooths the request rate within that budget. Both must stay
shared singletons -- a limiter or semaphore constructed fresh per call (e.g.
inside get_llm()) would give each group agent its own independent budget and
throttle nothing in aggregate.

requests_per_second/max_bucket_size and max_parallel_calls only throttle
request *count*, not token volume. gpt-5.4-mini's org TPM cap (200k) was
still hit at requests_per_second=2/max_bucket_size=4/max_parallel_calls=3 on
2026-08-09 because deep-agent tool-call turns carry large prompts -- lowered
here to reduce how often that cap gets hit at all, not just how many retries
are available once it does.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from langchain_core.rate_limiters import InMemoryRateLimiter


@dataclass(frozen=True)
class DeepAgentLimits:
    max_parallel_calls: int = 2


DEEPAGENT_LIMITS = DeepAgentLimits()
TARGET_SEMAPHORE = asyncio.Semaphore(DEEPAGENT_LIMITS.max_parallel_calls)

REMEDIATION_RATE_LIMITER = InMemoryRateLimiter(
    requests_per_second=1,
    check_every_n_seconds=0.1,
    max_bucket_size=2,
)

MAX_RETRIES = 6
