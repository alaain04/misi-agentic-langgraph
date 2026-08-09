from __future__ import annotations

import asyncio
import inspect
import json
import logging
import textwrap
import time
import uuid
from typing import cast

from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)
from src.main_graph.subgraphs.report.agents.critique import critique_report_finding
from src.main_graph.subgraphs.report.agents.impact_analysis_agent import (
    make_impact_analysis_tool,
)
from src.main_graph.tools.external_api import web_search
from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import (
    BlastRadiusSummary,
    FindingEnrichmentDecision,
    PrepResult,
    ReportFinding,
)
from src.utils.model_registry import AgentRole, get_role_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 4
_llm = get_role_llm(AgentRole.FINDING_ENRICHER)

_TOOL_DESCRIPTIONS = {
    "web_search": "web_search(query: str): search the web for advisories/issues/"
    "releases about this finding's SPECIFIC flagged reason (never a generic query)",
    "impact_analysis": "impact_analysis(depth: int = 3): investigates this "
    "package's real usage impact — affected files, whether usage reaches "
    "production, and which business use cases are actually affected. Always "
    "available.",
}

_SYSTEM = textwrap.dedent("""\
    You are a technical report writer enriching ONE dependency risk finding
    with grounded evidence. You may only investigate the package below —
    every tool call you make is forced server-side to target it regardless of
    what you pass, so there is no benefit in naming another package.

    Finding to enrich:
    - package: {dep_name}
    - severity: {severity}
    - description: {description}

    {directness_guidance}

    Available tools:
    {tool_descriptions}

    When you have enough evidence, set finalize=true and populate `finding`
    with a complete ReportFinding:
    - recommendation: an action the user can actually take. The user's ONLY
      levers are on DIRECT dependencies (declared in package.json). Follow the
      directness guidance above: for a direct package, recommend upgrading or
      replacing it; for a transitive package, recommend updating the direct
      dependent(s) named above — never an action on the transitive itself.
    - alternatives: ONLY packages backed by a web_search result; NEVER include
      any of these already-flagged packages: {excluded_alternatives}
    - business_impact: derived from impact_analysis's narrative/
      use_cases_impacted if present. If impact_analysis could not determine
      impact, say so — never invent file counts or guess.
    - evidence: only cite results that actually discuss this finding's own
      reason, never a generic tutorial that happens to mention the package.
    - affected_files: from impact_analysis output, if any.

    After {max_iter} iterations, set finalize=true regardless of coverage.
    """).strip()


def _build_tool_map(finding: FindingNote, prep: PrepResult, container) -> dict:
    return {
        "web_search": web_search,
        "impact_analysis": make_impact_analysis_tool(finding, prep, container),
    }


def _format_tools(tool_map: dict) -> str:
    return "\n".join(f"- {_TOOL_DESCRIPTIONS[name]}" for name in tool_map)


def _directness_guidance(
    dep_name: str, is_direct_dep: bool, dependents: list[str]
) -> str:
    if is_direct_dep:
        return (
            f"'{dep_name}' is a DIRECT dependency (declared in package.json). "
            "Recommend the concrete fix the user applies directly: upgrade to a "
            "fixed version, or replace it with a safer package."
        )
    parents = ", ".join(dependents) if dependents else "an unknown direct dependency"
    return (
        f"'{dep_name}' is a TRANSITIVE dependency. It is NOT in package.json and "
        f"the user cannot upgrade, replace, pin, or override it directly. It is "
        f"pulled in by these direct dependencies: {parents}.\n"
        "Anchor everything actionable on the direct dependent(s) above:\n"
        f'- recommendation MUST target the direct dependent(s), e.g. "update '
        f"<direct-dependent> to a version whose dependency tree no longer includes "
        f'{dep_name} (or resolves it to a fixed version)". The finding description '
        'may already carry the exact fix path from the audit (e.g. "Fix requires '
        'X@Y"); prefer it when present.\n'
        "- If no direct-dependent update resolves it (description says no fix is "
        f"available), say so honestly, then suggest replacing the direct "
        f"dependent(s) or accepting the risk — never patching {dep_name}.\n"
        f"- Do NOT suggest replacing, forking, or adding overrides/resolutions for "
        f"{dep_name}, and do NOT put {dep_name} in alternatives.\n"
        "- alternatives: leave empty unless proposing a replacement for a direct "
        "dependent."
    )


def _format_results(results: list[ToolResult]) -> str:
    if not results:
        return "No results yet."
    parts = []
    for tr in results[-10:]:
        val = (
            f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        )
        parts.append(f"[{tr.tool}] → {val}")
    return "\n\n".join(parts)


def _tool_callable(fn):
    """LangChain's @tool decorator stores a sync function's body in .func
    and an async function's body in .coroutine (leaving .func None) — pick
    whichever is actually callable for signature introspection."""
    func = getattr(fn, "func", None)
    if func is not None:
        return func
    coroutine = getattr(fn, "coroutine", None)
    if coroutine is not None:
        return coroutine
    return fn


async def _run_tool(tc: ToolCall, tool_map: dict, dep_name: str) -> ToolResult:
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=tc.args,
            output={},
            error=f"unknown tool: {tc.tool}",
            duration_ms=0,
        )
    kwargs = dict(tc.args)
    sig = inspect.signature(_tool_callable(fn))
    if "package_name" in sig.parameters:
        # Force-injected: this subagent can only ever fetch evidence for its
        # own finding's package, regardless of what the LLM passed.
        kwargs["package_name"] = dep_name
    try:
        output = (
            await fn.ainvoke(kwargs) if hasattr(fn, "ainvoke") else await fn(**kwargs)
        )
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=kwargs,
            output=output if isinstance(output, dict) else {"result": output},
            error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as exc:
        logger.warning("finding_enricher: tool %s failed: %s", tc.tool, exc)
        return ToolResult(
            id=str(uuid.uuid4()),
            tool=tc.tool,
            args=kwargs,
            output={},
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _feedback_result(feedback: str) -> ToolResult:
    return ToolResult(
        id=str(uuid.uuid4()),
        tool="verification_feedback",
        args={},
        output={"feedback": feedback},
        error=None,
        duration_ms=0,
    )


def _grounded_impact_analysis(
    tool_results: list[ToolResult],
) -> BlastRadiusSummary | None:
    for tr in tool_results:
        if tr.tool == "impact_analysis" and not tr.error:
            return BlastRadiusSummary(**tr.output)
    return None


def _fallback_finding(finding: FindingNote) -> ReportFinding:
    return ReportFinding(
        dep_name=finding.dep_name,
        severity=finding.severity,
        description=finding.description,
        recommendation="Review manually",
    )


async def enrich_finding(
    finding: FindingNote,
    prep: PrepResult,
    all_flagged_dep_names: list[str],
    container=None,
) -> tuple[ReportFinding, list[str]]:
    tool_map = _build_tool_map(finding, prep, container)
    tool_results: list[ToolResult] = []
    draft: ReportFinding | None = None
    excluded = (
        ", ".join(n for n in all_flagged_dep_names if n != finding.dep_name) or "none"
    )
    finding_is_direct = is_direct(prep.dependency_graph, finding.dep_name)
    dependents = (
        []
        if finding_is_direct
        else direct_dependents(prep.dependency_graph, finding.dep_name)
    )
    guidance = _directness_guidance(finding.dep_name, finding_is_direct, dependents)

    structured = _llm.with_structured_output(
        FindingEnrichmentDecision, method="function_calling"
    )

    for iteration in range(_MAX_ITERATIONS):
        system = _SYSTEM.format(
            dep_name=finding.dep_name,
            severity=finding.severity,
            description=finding.description,
            tool_descriptions=_format_tools(tool_map),
            excluded_alternatives=excluded,
            max_iter=_MAX_ITERATIONS,
            directness_guidance=guidance,
        )
        prompt = (
            f"Tool results so far:\n{_format_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        try:
            decision = cast(
                FindingEnrichmentDecision,
                await structured.ainvoke(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]
                ),
            )
        except AssertionError:
            raise
        except Exception as exc:
            logger.warning(
                "finding_enricher: structured decision failed, retrying: %s", exc
            )
            continue

        last = iteration == _MAX_ITERATIONS - 1
        if decision.finalize or last:
            draft = decision.finding or _fallback_finding(finding)
            grounded = _grounded_impact_analysis(tool_results)
            if grounded is not None:
                draft.blast_radius = grounded
                draft.affected_files = grounded.affected_files
            try:
                verdict = await critique_report_finding(finding, draft, tool_results)
            except Exception as exc:
                logger.warning(
                    "finding_enricher: critique failed, accepting draft: %s", exc
                )
                draft.trust = True
                draft.observation = ""
                break
            if verdict.ok:
                draft.trust = True
                draft.observation = ""
                break
            if last:
                draft.trust = False
                draft.observation = verdict.feedback
                break
            tool_results.append(_feedback_result(verdict.feedback))
            continue

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[
                    _run_tool(tc, tool_map, finding.dep_name)
                    for tc in decision.tool_calls
                ]
            )
            tool_results.extend(new_results)

    if draft is None:
        logger.warning(
            "finding_enricher: no successful decision for %s, using fallback",
            finding.dep_name,
        )
        draft = _fallback_finding(finding)
    draft.is_direct = finding_is_direct
    draft.direct_dependents = dependents
    return draft, [tr.tool for tr in tool_results]
