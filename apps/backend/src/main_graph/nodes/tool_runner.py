"""Tool runner node — executes conductor tool calls in parallel."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from langchain_core.runnables import RunnableConfig

from src.main_graph.state import MainState
from src.main_graph.tools.registry import TOOL_REGISTRY
from src.models.conductor import ToolCall, ToolResult

logger = logging.getLogger(__name__)


async def _run_tool(tc: ToolCall, repo_path: str) -> ToolResult:
    start = time.monotonic()
    fn = TOOL_REGISTRY.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=f"tool '{tc.tool}' not found in registry",
            duration_ms=0,
        )
    try:
        output = await fn(repo_path=repo_path, **tc.args)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output=output, error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("tool_runner: tool=%s failed: %s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


async def tool_runner(state: MainState, config: RunnableConfig) -> dict:
    decision = state.get("conductor_decision")
    if decision is None or not decision.tool_calls:
        return {"tool_results": []}

    repo_path = state.get("repo_path", "")
    tool_calls = decision.tool_calls

    logger.info("tool_runner: executing %d tools in parallel", len(tool_calls))
    results = await asyncio.gather(*[_run_tool(tc, repo_path) for tc in tool_calls])

    for tr in results:
        if tr.error:
            logger.warning("tool_runner: tool=%s error=%s", tr.tool, tr.error)
        else:
            logger.info("tool_runner: tool=%s duration_ms=%d", tr.tool, tr.duration_ms)

    return {"tool_results": list(results)}
