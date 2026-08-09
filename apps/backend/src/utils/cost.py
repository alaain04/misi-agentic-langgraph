"""LLM cost tracking via LangChain callback."""

import time
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

# USD per 1M tokens: {model: (input_rate, output_rate)}
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-5.4-nano-2026-03-17": (0.10, 0.40),
    "gpt-5.4-mini-2026-03-17": (0.40, 1.60),
    "gpt-5.5-2026-04-23": (2.50, 10.00),
}
_FALLBACK_RATE = (0.40, 1.60)
_ROLE_TAG_PREFIX = "agent_role:"
_UNTAGGED = "untagged"


def _role_from_tags(tags: list[str] | None) -> str:
    for tag in tags or []:
        if tag.startswith(_ROLE_TAG_PREFIX):
            return tag[len(_ROLE_TAG_PREFIX) :]
    return _UNTAGGED


def _extract_usage_and_model(response: LLMResult) -> tuple[dict, str]:
    """Real ChatOpenAI responses carry token_usage/model_name on
    `llm_output`. Some chat models (e.g. GenericFakeChatModel, used in
    tests) never populate `llm_output` at all and instead leave usage
    data on the generated message's `response_metadata`. Prefer
    `llm_output` (unchanged production behavior) and fall back to the
    per-generation message metadata so both shapes work."""
    llm_output = response.llm_output or {}
    usage = llm_output.get("token_usage") or {}
    model = llm_output.get("model_name", "")
    if not usage:
        for generation in response.generations:
            for gen in generation:
                message = getattr(gen, "message", None)
                metadata = getattr(message, "response_metadata", None) or {}
                fallback_usage = metadata.get("token_usage")
                if fallback_usage:
                    usage = fallback_usage
                    model = model or metadata.get("model_name", "")
                    break
            if usage:
                break
    return usage, model


class CostCallback(BaseCallbackHandler):
    """Accumulates token usage and computes USD cost across all LLM calls.

    Also keys usage by the calling AgentRole (via the `agent_role:<role>`
    tag `get_role_llm` binds on every LLM runnable — see
    src/utils/model_registry.py) so cost/latency can be attributed per role,
    not just summed globally. Calls with no such tag bucket under
    "untagged".
    """

    def __init__(self) -> None:
        super().__init__()
        self._cost: float = 0.0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self._breakdown: dict[str, dict] = {}
        self._start_times: dict[UUID, float] = {}

    def on_llm_start(self, serialized, prompts, *, run_id: UUID, **kwargs) -> None:
        self._start_times[run_id] = time.monotonic()

    def on_chat_model_start(
        self, serialized, messages, *, run_id: UUID, **kwargs
    ) -> None:
        self._start_times[run_id] = time.monotonic()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        **kwargs,
    ) -> None:
        usage, model = _extract_usage_and_model(response)
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        input_rate, output_rate = _PRICING.get(model, _FALLBACK_RATE)
        call_cost = (prompt * input_rate + completion * output_rate) / 1_000_000

        self._cost += call_cost
        self.prompt_tokens += prompt
        self.completion_tokens += completion

        start = self._start_times.pop(run_id, None)
        latency_ms = (time.monotonic() - start) * 1000 if start is not None else 0.0

        role = _role_from_tags(tags)
        bucket = self._breakdown.setdefault(
            role,
            {
                "cost": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "call_count": 0,
                "latency_ms": 0.0,
            },
        )
        bucket["cost"] += call_cost
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion
        bucket["call_count"] += 1
        bucket["latency_ms"] += latency_ms

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost(self) -> float:
        return round(self._cost, 6)

    def breakdown(self) -> dict[str, dict]:
        """Per-role usage snapshot. Rounds cost/latency for readability;
        callers needing full precision should read `.cost()` for the total."""
        return {
            role: {
                "cost": round(b["cost"], 6),
                "prompt_tokens": b["prompt_tokens"],
                "completion_tokens": b["completion_tokens"],
                "call_count": b["call_count"],
                "latency_ms": round(b["latency_ms"], 1),
            }
            for role, b in self._breakdown.items()
        }
