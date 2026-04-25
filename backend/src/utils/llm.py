"""LLM factory.

Consumers pick a model from the `Model` enum and call `get_llm(model)`.
Adding a new provider means adding enum members and a branch in `get_llm` —
nothing else changes.

Install notes per provider:
    OpenAI:    langchain-openai (already in deps)
    Anthropic: uv add langchain-anthropic
    Google:    uv add langchain-google-genai
"""

from enum import StrEnum

from langchain_core.language_models import BaseChatModel

from src.utils.config import settings


class Model(StrEnum):
    # OpenAI
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4O = "gpt-4o"


def get_llm(model: Model = Model.GPT_4O_MINI) -> BaseChatModel:
    """Return a configured chat model for the given ``model`` enum value.

    Providers other than OpenAI require their package to be installed first.
    Importing is deferred so missing packages only raise at call time, not at
    import time for callers that never use those models.
    """
    if model in (Model.GPT_4O_MINI, Model.GPT_4O):
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        return ChatOpenAI(model=model, api_key=settings.openai_api_key, temperature=0)

    raise ValueError(f"No provider configured for model {model!r}")
