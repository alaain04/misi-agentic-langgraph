"""LLM cost tracking via LangChain callback."""

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


class CostCallback(BaseCallbackHandler):
    """Accumulates token usage and computes USD cost across all LLM calls."""

    def __init__(self) -> None:
        super().__init__()
        self._cost: float = 0.0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        usage = (response.llm_output or {}).get("token_usage", {})
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        model = (response.llm_output or {}).get("model_name", "")
        input_rate, output_rate = _PRICING.get(model, _FALLBACK_RATE)
        self._cost += (prompt * input_rate + completion * output_rate) / 1_000_000
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost(self) -> float:
        return round(self._cost, 6)
