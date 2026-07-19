from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.report.state import ReportState
from src.models.results import ReportFinding, ReportResult
from src.utils.llm import Model, get_llm, parse_llm_json
from src.utils.severity import SEVERITY_ORDER

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a technical report writer. You are given a list of already-vetted
dependency risk findings — each already carries its own recommendation,
alternatives, business_impact, and evidence, produced and critiqued by an
independent per-finding agent. Do not alter, add, or remove any finding;
write only the report-level narrative.

Output ONLY valid JSON:
{
  "executive_summary": "<2-4 sentence summary across all findings>",
  "recommendations": ["<top-level recommendation>"]
}
"""


def _format_findings(findings: list[ReportFinding]) -> str:
    if not findings:
        return "No findings."
    parts = []
    for f in findings:
        trust_note = "" if f.trust else f" [UNTRUSTED: {f.observation}]"
        parts.append(
            f"- [{f.severity.upper()}] {f.dep_name}: {f.description}{trust_note}"
        )
    return "\n".join(parts)


async def report_synthesizer(state: ReportState, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    findings = [ReportFinding(**f) for f in (state.get("enriched_findings") or [])]

    user_prompt = (
        f"Concern: {state['concern']}\n\nFindings:\n{_format_findings(findings)}"
    )

    try:
        response = await _llm.ainvoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_prompt},
            ]
        )
        content = response.content if isinstance(response.content, str) else ""
        data = parse_llm_json(content)
    except Exception as exc:
        logger.warning("report_synthesizer: narrative generation failed: %s", exc)
        data = {"executive_summary": "", "recommendations": []}

    overall = max(
        (f.severity for f in findings),
        key=lambda s: SEVERITY_ORDER.get(s, 0),
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
        "report_synthesizer: saved report_result_id=%s findings=%d",
        report_result_id,
        len(findings),
    )
    return {"report_result_id": report_result_id}
