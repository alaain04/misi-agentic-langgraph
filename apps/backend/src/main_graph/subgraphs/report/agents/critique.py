from __future__ import annotations

import textwrap
from typing import cast

from pydantic import BaseModel

from src.models.conductor import FindingNote, ToolResult
from src.models.results import ReportFinding
from src.utils.model_registry import AgentRole, get_role_llm

_llm = get_role_llm(AgentRole.REPORT_CRITIQUE)

_SYSTEM = textwrap.dedent("""\
    You are an evidence auditor for a dependency risk report. You are given
    one finding's original claim (from the analysis phase) and a draft
    ReportFinding an isolated enrichment agent produced from its own tool
    results.

    Judge whether the draft is adequately supported by ITS OWN tool_results:
    - evidence entries must reference something that actually appears in
      tool_results, not invented and not generic.
    - business_impact must be grounded in impact_analysis output (its
      narrative/use_cases_impacted, not invented) present in tool_results.
      If impact_analysis returned nothing usable, business_impact should say
      so rather than guess — flag it if it guesses instead.
    - alternatives must be backed by a web_search result in tool_results.
    - severity and dep_name must be unchanged from the original finding.

    Output a FindingVerdict:
    - ok: true only if the draft is fully supported by tool_results.
    - feedback: concrete and actionable — name exactly what is missing or
      overstated. Empty string when ok is true.
    - calibrated_confidence: 0.0-1.0 based on evidence quality.
    """).strip()


class FindingVerdict(BaseModel):
    ok: bool
    feedback: str
    calibrated_confidence: float


def _format_tool_results(tool_results: list[ToolResult]) -> str:
    if not tool_results:
        return "(no tool results)"
    parts = []
    for tr in tool_results:
        val = f"ERROR: {tr.error}" if tr.error else str(tr.output)[:1000]
        parts.append(f"[{tr.tool}] {val}")
    return "\n\n".join(parts)


async def critique_report_finding(
    original: FindingNote,
    draft: ReportFinding,
    tool_results: list[ToolResult],
) -> FindingVerdict:
    user = (
        f"Original finding: {original.dep_name} [{original.severity}] "
        f"{original.description}\n\n"
        f"Draft report finding:\n{draft.model_dump_json(indent=2)}\n\n"
        f"Tool results this agent collected:\n{_format_tool_results(tool_results)}"
    )
    structured = _llm.with_structured_output(FindingVerdict, method="function_calling")
    return cast(
        FindingVerdict,
        await structured.ainvoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ]
        ),
    )
