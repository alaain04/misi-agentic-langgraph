from __future__ import annotations

import asyncio
import logging
import time
import uuid

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.utils.registry import REPORT_TOOL_HANDLERS
from src.models.conductor import ToolCall, ToolResult
from src.models.results import AnalysisResult, PrepResult

logger = logging.getLogger(__name__)


async def _run_one(
    tc: ToolCall, prep: PrepResult, analysis: AnalysisResult
) -> ToolResult:
    start = time.monotonic()
    handler = REPORT_TOOL_HANDLERS.get(tc.tool)
    try:
        if handler is None:
            output = {"error": f"unknown tool: {tc.tool}"}
        else:
            output = await handler(tc.args, prep, analysis)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output=output, error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("report_tool_runner: tool=%s error=%s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
            output={}, error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


async def report_tool_runner(state, config: RunnableConfig) -> dict:
    decision = state.get("conductor_decision")
    if not decision or not decision.tool_calls:
        return {"tool_results": []}

    dao = get_services(config)["result_dao"]
    prep: PrepResult = await dao.get_prep(state["prep_result_id"])
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    results = await asyncio.gather(
        *[_run_one(tc, prep, analysis) for tc in decision.tool_calls]
    )
    return {"tool_results": list(results)}
