"""Report builder — single LLM call that formats accumulated FindingNote entries into a report."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from src.main_graph.state import MainState
from src.models.conductor import FindingNote
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_SYSTEM = """\
You are a technical report writer. Given structured investigation findings, produce a JSON analysis report.

Output ONLY valid JSON matching this exact shape:
{
  "executive_summary": "<2-4 sentence summary of overall risk>",
  "overall_risk_level": "<critical|high|medium|low|none>",
  "findings": [
    {
      "dep_name": "<package name>",
      "severity": "<critical|high|medium|low|info>",
      "description": "<concise description>",
      "recommendation": "<actionable fix>",
      "evidence": [{"tool": "<tool>", "url": "<url or null>", "log_snippet": "<excerpt>"}]
    }
  ],
  "recommendations": ["<deduplicated list of top recommendations>"]
}
"""


def _format_findings(findings: list[FindingNote]) -> str:
    return json.dumps(
        [
            {
                "dep_name": f.dep_name,
                "severity": f.severity,
                "description": f.description,
                "evidence": [e.model_dump() for e in f.evidence],
            }
            for f in findings
        ],
        indent=2,
    )


def _overall_risk(findings: list[FindingNote]) -> str:
    if not findings:
        return "none"
    return max(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0)).severity


async def report_builder(state: MainState) -> dict:
    findings = state.get("findings") or []
    concern = state.get("concern", "")

    if not findings:
        report = {
            "concern": concern,
            "generated_at": datetime.now(UTC).isoformat(),
            "overall_risk_level": "none",
            "executive_summary": "No significant findings were identified during the investigation.",
            "findings": [],
            "recommendations": [],
        }
        return {"analysis_report": report}

    sorted_findings = sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0), reverse=True)

    response = await _llm.ainvoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Concern: {concern}\n\nFindings:\n{_format_findings(sorted_findings)}"},
    ])

    try:
        report_data = parse_llm_json(response.content or "")
    except Exception:
        report_data = {"executive_summary": response.content, "findings": [], "recommendations": []}

    report = {
        "concern": concern,
        "generated_at": datetime.now(UTC).isoformat(),
        "overall_risk_level": _overall_risk(findings),
        **report_data,
    }

    logger.info("report_builder: findings=%d overall_risk=%s", len(findings), report["overall_risk_level"])
    return {"analysis_report": report}
