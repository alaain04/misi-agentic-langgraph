"""Tool registry — maps tool names to async callables and descriptions.

Tools are populated after all tool modules are imported.
Each tool is: async (repo_path: str, **kwargs) -> dict
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

# Populated by each tool module at import time via register()
TOOL_REGISTRY: dict[str, Callable[..., Awaitable[dict]]] = {}
TOOL_DESCRIPTIONS: dict[str, str] = {}


def register(name: str, description: str):
    """Decorator that registers an async tool function by name."""
    def decorator(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        TOOL_REGISTRY[name] = fn
        TOOL_DESCRIPTIONS[name] = description
        return fn
    return decorator
