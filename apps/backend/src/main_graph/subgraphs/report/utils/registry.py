from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.main_graph.tools.code_impact import make_code_impact_tool
from src.main_graph.tools.external_api import web_search
from src.models.results import AnalysisResult, PrepResult

REPORT_TOOL_HANDLERS: dict[str, Callable[..., Awaitable[dict]]] = {}
REPORT_TOOL_DESCRIPTIONS: dict[str, str] = {}


def _register(name: str, description: str):
    def decorator(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        REPORT_TOOL_HANDLERS[name] = fn
        REPORT_TOOL_DESCRIPTIONS[name] = description
        return fn
    return decorator


@_register("web_search", "search for alternatives, CVE details, migration guides")
async def _web_search_handler(
    args: dict, prep: PrepResult, analysis: AnalysisResult
) -> dict:
    return await web_search(**args)


@_register("code_impact", "find source files that import or use a specific npm package")
async def _code_impact_handler(
    args: dict, prep: PrepResult, analysis: AnalysisResult
) -> dict:
    impact_tool = make_code_impact_tool(prep.vector_store_id)
    output = await impact_tool.ainvoke(args)
    return output if isinstance(output, dict) else {"results": output}


@_register(
    "get_findings",
    "retrieve findings filtered by severity (critical|high|medium|low|all)",
)
async def _get_findings_handler(
    args: dict, prep: PrepResult, analysis: AnalysisResult
) -> dict:
    severity = args.get("severity", "all")
    findings = analysis.findings
    if severity != "all":
        findings = [f for f in findings if f.severity == severity]
    return {"findings": [f.model_dump() for f in findings]}
