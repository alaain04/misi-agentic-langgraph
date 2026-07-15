from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.constants import REPORT
from src.models.conductor import ToolResult
from src.models.results import AnalysisResult, ReportDraft, ReportFinding, ReportResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_SYSTEM = """\
You are a technical report writer. Given dependency risk findings and enrichment data
(web search results + code impact), produce a report.

For each finding, provide a concise description, an actionable recommendation,
safer alternatives if any, affected files if known, and supporting evidence
(tool name, url if any, and a short log excerpt).
"""


def _format_enrichment(tool_results: list[ToolResult]) -> str:
    return "\n\n".join(
        f"[{tr.tool}({json.dumps(tr.args)})] -> "
        f"{json.dumps(tr.output, indent=2)[:1500]}"
        for tr in tool_results if not tr.error
    )


async def save_report_result(state, config: RunnableConfig) -> dict:
    services = get_services(config)
    dao = services["result_dao"]
    job_repo = services["job_repo"]
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])
    tool_results: list[ToolResult] = state.get("tool_results") or []

    findings_json = json.dumps([f.model_dump() for f in analysis.findings], indent=2)
    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings:\n{findings_json}\n\n"
        f"Enrichment data:\n{_format_enrichment(tool_results) or 'None'}"
    )

    structured = _llm.with_structured_output(ReportDraft, method="function_calling")
    try:
        draft: ReportDraft = await structured.ainvoke([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_prompt},
        ])
        findings = draft.findings
        executive_summary = draft.executive_summary
        recommendations = draft.recommendations
    except Exception as exc:
        logger.warning("save_report_result: structured output failed: %s", exc)
        findings = [
            ReportFinding(
                dep_name=f.dep_name,
                severity=f.severity,
                description=f.description,
                recommendation="Review manually",
            )
            for f in analysis.findings
        ]
        executive_summary = ""
        recommendations = []

    overall = max(
        (f.severity for f in analysis.findings),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
        default="none",
    )

    result = ReportResult(
        job_id=state["job_id"],
        concern=state["concern"],
        executive_summary=executive_summary,
        overall_risk_level=overall,
        findings=findings,
        recommendations=recommendations,
    )
    report_result_id = await dao.save_report(result)

    await job_repo.update_artifact_data(
        state["job_id"],
        REPORT,
        {"tool_results": [tr.model_dump() for tr in tool_results]},
    )

    logger.info("save_report_result: saved report_result_id=%s findings=%d",
                report_result_id, len(findings))
    return {"report_result_id": report_result_id}
