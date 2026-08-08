"""LLM factory and response utilities.

Consumers pick a model from the `Model` enum and call `get_llm(model)`.
Adding a new provider means adding enum members and a branch in `get_llm` —
nothing else changes.

Install notes per provider:
    OpenAI:    langchain-openai (already in deps)
    Anthropic: uv add langchain-anthropic
    Google:    uv add langchain-google-genai
"""

import json
from enum import StrEnum
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import BaseRateLimiter

from src.utils.config import settings


class Model(StrEnum):
    # OpenAI
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4O = "gpt-4o"
    GPT_5_4_NANO = "gpt-5.4-nano-2026-03-17"
    GPT_5_4_MINI = "gpt-5.4-mini-2026-03-17"
    GPT_5_4 = "gpt-5.5-2026-04-23"


def get_llm(
    model: Model = Model.GPT_4O_MINI,
    *,
    rate_limiter: BaseRateLimiter | None = None,
    max_retries: int | None = None,
) -> BaseChatModel:
    """Return a configured chat model for the given ``model`` enum value.

    Providers other than OpenAI require their package to be installed first.
    Importing is deferred so missing packages only raise at call time, not at
    import time for callers that never use those models.

    ``rate_limiter`` must be a caller-owned shared instance (e.g. a
    module-level ``InMemoryRateLimiter``) when the goal is to throttle
    aggregate throughput across multiple concurrent callers -- a limiter
    constructed fresh per `get_llm()` call gives each caller its own
    independent token bucket and throttles nothing in aggregate.
    ``max_retries`` overrides the provider client's default retry-with-
    backoff budget on transient errors (e.g. 429s); ``None`` keeps the
    provider default.
    """
    from langchain_openai import ChatOpenAI  # noqa: PLC0415

    return ChatOpenAI(
        model=model.value,
        api_key=settings.openai_api_key,
        temperature=0,
        rate_limiter=rate_limiter,
        max_retries=max_retries,
    )


def parse_llm_json(text: str) -> Any:
    """Strip markdown code fences from an LLM response and parse as JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)
