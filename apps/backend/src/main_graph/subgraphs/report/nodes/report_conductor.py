from __future__ import annotations

import json
import logging
import textwrap

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.models.conductor import FindingNote, ToolResult
from src.models.results import AnalysisResult, ReportConductorDecision
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 6
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = textwrap.dedent("""\
    You are a technical report writer. You enrich dependency risk findings with:
    1. web_search — find safer alternatives and migration guides for risky packages
    2. code_impact — find which source files use each risky package
    3. get_findings — retrieve findings filtered by severity

    For each high/critical finding, call both web_search and code_impact
    before finalizing.
    Output a ReportConductorDecision:
    - tool_calls: tools to run in parallel
    - finalize: true when all high/critical findings are enriched
    - reasoning: what you are doing

    After {max_iter} iterations, set finalize=true.

    Available tools:
    - web_search(query): search for alternatives, CVE details, migration guides
    - code_impact(package_name): find source files importing the package
    - get_findings(severity): retrieve findings (severity: critical|high|medium|low|all)
    """).strip()


def _format_results(results: list[ToolResult]) -> str:
    if not results:
        return "No tool results yet."
    parts = []
    for tr in results[-15:]:
        val = (
            f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        )
        parts.append(f"[{tr.tool}] → {val}")
    return "\n\n".join(parts)


def _format_findings(findings: list[FindingNote]) -> str:
    return "\n".join(
        f"- [{f.severity.upper()}] {f.dep_name}: {f.description}" for f in findings
    )


async def report_conductor(state, config: RunnableConfig) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1
    dao = get_services(config)["result_dao"]

    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])

    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings to enrich:\n{_format_findings(analysis.findings)}\n\n"
        f"Tool results so far:\n{_format_results(state.get('tool_results') or [])}\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )
    system = _SYSTEM.format(max_iter=_MAX_ITERATIONS)
    structured = _llm.with_structured_output(
        ReportConductorDecision, method="function_calling"
    )
    decision: ReportConductorDecision = await structured.ainvoke(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
    )

    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    logger.info(
        "report_conductor: iteration=%d tools=%d finalize=%s",
        iteration,
        len(decision.tool_calls),
        decision.finalize,
    )
    return {"conductor_decision": decision, "conductor_iteration": iteration}
