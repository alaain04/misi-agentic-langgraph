from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid

from src.models.conductor import FindingNote, ToolCall, ToolResult
from src.models.results import AgentDispatch, DomainAgentDecision, EvidenceBundle, PrepResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 6
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_GENERIC = """\
You are a domain specialist investigating dependency risks for a Node.js project.
Your domain: {domain}
Hypothesis to investigate: {hypothesis}
Packages to focus on: {packages}
Project context: {context}

Each iteration output a DomainAgentDecision:
- tool_calls: tools to run in parallel (empty when done)
- findings: FindingNote list for risks discovered so far
- summary: concise summary of what you found
- confidence: float 0-1 reflecting evidence strength
- finalize: true when you have enough evidence
- reasoning: brief explanation of your next step

Available tools:
{tool_descriptions}

Rules:
- Never repeat a tool call with the same arguments.
- Populate evidence in each FindingNote with tool/url/log_snippet.
- Set finalize=true when confidence > 0.7 or you have exhausted relevant tools.
- After {max_iter} iterations, set finalize=true regardless.
"""


def _format_tools(tools: list) -> str:
    lines = []
    for t in tools:
        desc = getattr(t, "description", "") or ""
        lines.append(f"- {getattr(t, 'name', t.__name__)}: {desc}")
    return "\n".join(lines) or "No tools available."


def _format_results(results: list[ToolResult]) -> str:
    if not results:
        return "No results yet."
    parts = []
    for tr in results[-10:]:
        val = f"ERROR: {tr.error}" if tr.error else json.dumps(tr.output, indent=2)[:1500]
        parts.append(f"[{tr.tool}] → {val}")
    return "\n\n".join(parts)


async def _run_tool(tc: ToolCall, tool_map: dict, prep: PrepResult) -> ToolResult:
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output={}, error=f"unknown tool: {tc.tool}", duration_ms=0)
    try:
        import inspect
        sig = inspect.signature(fn.func if hasattr(fn, "func") else fn)
        kwargs = dict(tc.args)
        if "repo_path" in sig.parameters:
            kwargs["repo_path"] = prep.repo_path
        output = await fn.ainvoke(kwargs) if hasattr(fn, "ainvoke") else await fn(**kwargs)
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output=output if isinstance(output, dict) else {"result": output},
                          error=None, duration_ms=int((time.monotonic() - start) * 1000))
    except Exception as exc:
        logger.warning("base_agent tool %s failed: %s", tc.tool, exc)
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output={}, error=str(exc),
                          duration_ms=int((time.monotonic() - start) * 1000))


async def run_react_loop(
    dispatch: AgentDispatch,
    prep: PrepResult,
    tools: list,
    system_prompt: str | None = None,
) -> EvidenceBundle:
    tool_map = {getattr(t, "name", t.__name__): t for t in tools}
    tool_results: list[ToolResult] = []
    decision: DomainAgentDecision | None = None

    structured = _llm.with_structured_output(DomainAgentDecision, method="function_calling")
    template = system_prompt or _SYSTEM_GENERIC

    for iteration in range(_MAX_ITERATIONS):
        prompt = (
            f"Tool results so far:\n{_format_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        system = template.format(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages=", ".join(dispatch.packages_to_focus) or "all dependencies",
            context=prep.discovery_summary[:500],
            tool_descriptions=_format_tools(tools),
            max_iter=_MAX_ITERATIONS,
        )
        decision = await structured.ainvoke([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])

        if decision.finalize or iteration == _MAX_ITERATIONS - 1:
            break

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[_run_tool(tc, tool_map, prep) for tc in decision.tool_calls]
            )
            tool_results.extend(new_results)

    return EvidenceBundle(
        domain=dispatch.domain,
        hypothesis=dispatch.hypothesis,
        findings=decision.findings if decision else [],
        summary=decision.summary if decision else "No results.",
        confidence=decision.confidence if decision else 0.0,
    )
