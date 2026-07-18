from __future__ import annotations

import asyncio
import logging
import time
import uuid

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.tools.code_impact import make_code_impact_tool
from src.main_graph.tools.external_api import web_search
from src.models.conductor import ToolCall, ToolResult
from src.models.results import AnalysisResult, PrepResult

logger = logging.getLogger(__name__)


async def _get_findings_tool(severity: str, analysis: AnalysisResult) -> dict:
    findings = analysis.findings
    if severity != "all":
        findings = [f for f in findings if f.severity == severity]
    return {"findings": [f.model_dump() for f in findings]}


async def _run_one(
    tc: ToolCall, prep: PrepResult, analysis: AnalysisResult
) -> ToolResult:
    start = time.monotonic()
    try:
        if tc.tool == "get_findings":
            output = await _get_findings_tool(tc.args.get("severity", "all"), analysis)
        elif tc.tool == "web_search":
            output = await web_search(**tc.args)
        elif tc.tool == "code_impact":
            impact_tool = make_code_impact_tool(prep.vector_store_id)
            output = await impact_tool.ainvoke(tc.args)
            if not isinstance(output, dict):
                output = {"results": output}
        else:
            output = {"error": f"unknown tool: {tc.tool}"}
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output=output,
            error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("report_tool_runner: tool=%s error=%s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output={},
            error=str(exc),
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
