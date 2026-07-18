from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.models.conductor import ToolResult
from src.models.results import AnalysisResult, ReportFinding, ReportResult
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_SYSTEM = """\
You are a technical report writer. Given dependency risk findings and enrichment data
(web search results + code impact), produce a JSON report.

Output ONLY valid JSON:
{
  "executive_summary": "<2-4 sentence summary>",
  "overall_risk_level": "<critical|high|medium|low|none>",
  "findings": [
    {
      "dep_name": "<package>",
      "severity": "<critical|high|medium|low|info>",
      "description": "<concise description>",
      "recommendation": "<actionable fix>",
      "alternatives": ["<alternative package>"],
      "affected_files": ["<file:line>"],
      "evidence": [{"tool": "<tool>", "url": "<url or null>", "log_snippet": "<excerpt>"}]
    }
  ],
  "recommendations": ["<top-level recommendation>"]
}
"""


async def save_report_result(state, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])
    tool_results: list[ToolResult] = state.get("tool_results") or []

    enrichment = "\n\n".join(
        f"[{tr.tool}({json.dumps(tr.args)})] → {json.dumps(tr.output, indent=2)[:1500]}"
        for tr in tool_results
        if not tr.error
    )

    findings_json = json.dumps([f.model_dump() for f in analysis.findings], indent=2)
    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings:\n{findings_json}\n\n"
        f"Enrichment data:\n{enrichment or 'None'}"
    )

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
    )

    try:
        data = parse_llm_json(response.content or "")
        findings = [ReportFinding(**f) for f in data.get("findings", [])]
    except Exception:
        findings = [
            ReportFinding(
                dep_name=f.dep_name,
                severity=f.severity,
                description=f.description,
                recommendation="Review manually",
            )
            for f in analysis.findings
        ]
        data = {}

    overall = max(
        (f.severity for f in analysis.findings),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
        default="none",
    )

    result = ReportResult(
        job_id=state["job_id"],
        concern=state["concern"],
        executive_summary=data.get("executive_summary", ""),
        overall_risk_level=overall,
        findings=findings,
        recommendations=data.get("recommendations", []),
    )
    report_result_id = await dao.save_report(result)
    logger.info(
        "save_report_result: saved report_result_id=%s findings=%d",
        report_result_id,
        len(findings),
    )
    return {"report_result_id": report_result_id}
