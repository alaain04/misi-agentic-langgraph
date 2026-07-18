from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.models.conductor import ToolResult
from src.models.results import (
    AnalysisResult,
    BlastRadiusSummary,
    ReportFinding,
    ReportResult,
)
from src.utils.config import settings
from src.utils.llm import Model, get_llm, parse_llm_json
from src.utils.severity import SEVERITY_ORDER, filter_by_min_severity

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_BLAST_RADIUS_FIELDS = set(BlastRadiusSummary.model_fields)

_SYSTEM = """\
You are a technical report writer. Given dependency risk findings and enrichment data
(web search results + blast-radius/code-impact data), produce a JSON report.

business_impact must be derived from that dependency's blast_radius data if present
(e.g. affected_file_count, isolated_to_tests_or_scripts) — do not invent file counts
or reach claims. If blast_radius is unavailable but code_impact snippets are present,
read the surrounding code in those snippets (the enclosing function/class/module
name, what it computes or handles) and translate that into a plain-language business
consequence a non-technical stakeholder can understand — name the capability at risk
(e.g. "used in the checkout flow's tax calculation"), not the code mechanics (do not
say "imported in file X" — say what that file does). Never cite package.json or a
lockfile as a usage site; a dependency being declared there is expected and not a
finding. If neither blast_radius nor code_impact has anything, say the business
impact could not be determined rather than guessing.

Only include an item in alternatives if it is backed by a web_search result for that
dependency — leave alternatives empty rather than inventing a package name.

Check the full list of findings before writing each one: never suggest, as an
alternative to some dependency, another dependency that itself has a finding
somewhere in this same report — that is self-contradictory (you would be recommending
a move to something you're simultaneously flagging as risky). Prefer an alternative
with no finding in this report, or state that none was found.

Before citing a web_search hit in evidence, check it actually supports the finding's
severity and reason. A "how to install/use this package" tutorial does not support a
vulnerability, license, or maintenance-risk finding, even if it mentions the package
name — omit it rather than citing an irrelevant result. Only cite web_search hits
that discuss the specific risk (the CVE/advisory, the license conflict, the
abandonment) raised in the finding's description.

Every affected_files entry and every evidence entry for a finding must be about that
finding's own dep_name specifically — never carry over a file or web result that
belongs to a different package's enrichment data, even if it appears nearby in the
enrichment section.

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
      "business_impact": "<1-2 sentence narrative grounded in blast_radius data>",
      "evidence": [{"tool": "<tool>", "url": "<url or null>", "log_snippet": \
"<excerpt>"}]
    }
  ],
  "recommendations": ["<top-level recommendation>"]
}
"""


def _package_name_variants(package_name: str) -> set[str]:
    variants = {package_name.lower()}
    variants.add(package_name.lstrip("@").split("/")[-1].lower())
    return variants


def _evidence_matches_dep(evidence: dict, dep_name: str) -> bool:
    text = (
        str(evidence.get("log_snippet", "")) + " " + str(evidence.get("url", ""))
    ).lower()
    return any(v in text for v in _package_name_variants(dep_name))


def _drop_mismatched_evidence(findings: list[ReportFinding]) -> list[ReportFinding]:
    """Deterministic guard: the LLM can still misattribute evidence across
    findings even when instructed not to, so strip evidence entries that don't
    actually mention the finding's own package."""
    for finding in findings:
        finding.evidence = [
            e
            for e in finding.evidence
            if isinstance(e, dict) and _evidence_matches_dep(e, finding.dep_name)
        ]
    return findings
def _group_enrichment_by_dep(
    tool_results: list[ToolResult], dep_names: list[str]
) -> dict:
    """Attribute each tool result to the dependency it enriches.

    blast_radius calls carry an explicit package_name. web_search calls only
    carry a free-text query, so we attribute them heuristically by substring
    match; anything unattributed lands in "general".
    """
    by_dep: dict[str, dict[str, list]] = {name: {} for name in dep_names}
    general: list[dict] = []

    for tr in tool_results:
        if tr.error:
            continue
        entry = {"args": tr.args, "output": tr.output}
        if tr.tool == "blast_radius":
            dep = tr.args.get("package_name")
            if dep in by_dep:
                by_dep[dep].setdefault("blast_radius", []).append(entry)
                continue
        elif tr.tool == "code_impact":
            dep = tr.args.get("package_name")
            if dep in by_dep:
                by_dep[dep].setdefault("code_impact", []).append(entry)
                continue
        elif tr.tool == "web_search":
            query = str(tr.args.get("query", "")).lower()
            matched = [name for name in dep_names if name.lower() in query]
            if matched:
                for dep in matched:
                    by_dep[dep].setdefault("web_search_hits", []).append(entry)
                continue
        general.append({"tool": tr.tool, **entry})

    return {"by_dependency": by_dep, "general": general}


def _grounded_blast_radius(
    tool_results: list[ToolResult], dep_name: str
) -> BlastRadiusSummary | None:
    for tr in tool_results:
        if (
            tr.tool == "blast_radius"
            and not tr.error
            and tr.args.get("package_name") == dep_name
            and tr.output.get("available")
        ):
            return BlastRadiusSummary(
                **{k: v for k, v in tr.output.items() if k in _BLAST_RADIUS_FIELDS}
            )
    return None


async def save_report_result(state, config: RunnableConfig) -> dict:
    dao = get_services(config)["result_dao"]
    analysis: AnalysisResult = await dao.get_analysis(state["analysis_result_id"])
    tool_results: list[ToolResult] = state.get("tool_results") or []

    dep_names = sorted({f.dep_name for f in analysis.findings})
    enrichment = _group_enrichment_by_dep(tool_results, dep_names)

    findings_json = json.dumps([f.model_dump() for f in analysis.findings], indent=2)
    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Findings:\n{findings_json}\n\n"
        f"Enrichment data (grouped by dependency):\n"
        f"{json.dumps(enrichment, indent=2, default=str)[:8000]}"
    )

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_prompt},
        ]
    )

    try:
        content = response.content if isinstance(response.content, str) else ""
        data = parse_llm_json(content)
        findings = _drop_mismatched_evidence(
            [ReportFinding(**f) for f in data.get("findings", [])]
        )
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

    has_web_search = any(
        tr.tool == "web_search" and not tr.error for tr in tool_results
    )
    # A dependency already flagged elsewhere in this same report is itself a
    # known risk, so it can never coherently be suggested as the "safe"
    # alternative to a different flagged dependency.
    flagged_dep_names_lower = {name.lower() for name in dep_names}
    for finding in findings:
        if not has_web_search:
            finding.alternatives = []
        finding.alternatives = [
            alt
            for alt in finding.alternatives
            if alt.lower() not in flagged_dep_names_lower
        ]
        grounded = _grounded_blast_radius(tool_results, finding.dep_name)
        if grounded is not None:
            finding.blast_radius = grounded
            finding.affected_files = grounded.affected_files

    findings = filter_by_min_severity(findings, settings.risk_min_severity)

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
        "save_report_result: saved report_result_id=%s findings=%d",
        report_result_id,
        len(findings),
    )
    return {"report_result_id": report_result_id}
