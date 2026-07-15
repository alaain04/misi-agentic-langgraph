from __future__ import annotations
import asyncio
import inspect
import json
import logging
import textwrap
import time
import uuid
from abc import ABC, abstractmethod
from typing import ClassVar

from src.main_graph.subgraphs.analysis.agents.critique_agent import critique_findings
from src.main_graph.subgraphs.analysis.utils.dependency_versions import resolve_installed_versions
from src.main_graph.tools.registry import TOOL_DESCRIPTIONS
from src.main_graph.tools.search_code import make_search_code_tool
from src.models.conductor import ToolCall, ToolResult
from src.models.results import AgentDispatch, DomainAgentDecision, EvidenceBundle, PrepResult
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 6
_llm = get_llm(Model.GPT_5_4_MINI)


def _format_params(t) -> str:
    """Render a tool's real argument names/types so the LLM doesn't have to guess them."""
    if hasattr(t, "args"):  # LangChain StructuredTool (e.g. search_code)
        parts = []
        for pname, schema in t.args.items():
            ptype = schema.get("type", "any")
            if "default" in schema:
                parts.append(f"{pname}: {ptype} = {schema['default']!r}")
            else:
                parts.append(f"{pname}: {ptype}")
        return ", ".join(parts)
    try:
        sig = inspect.signature(t)
    except (TypeError, ValueError):
        return ""
    parts = []
    for p in sig.parameters.values():
        if p.name in ("repo_path", "detected_package_manager"):  # auto-injected by _run_tool, not supplied by the LLM
            continue
        ann = p.annotation
        if ann is inspect.Parameter.empty:
            ptype = ""
        elif isinstance(ann, str):  # `from __future__ import annotations` stringifies types
            ptype = ann
        else:
            ptype = getattr(ann, "__name__", str(ann))
        piece = f"{p.name}: {ptype}" if ptype else p.name
        if p.default is not inspect.Parameter.empty:
            piece += f" = {p.default!r}"
        parts.append(piece)
    return ", ".join(parts)


def _format_packages(names: list[str], dependency_graph: dict) -> str:
    if not names:
        return "all dependencies"
    installed = resolve_installed_versions(names, dependency_graph)
    return ", ".join(f"{n}@{installed[n]}" if n in installed else n for n in names)


def _format_tools(tools: list) -> str:
    lines = []
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", repr(t))
        desc = getattr(t, "description", "") or TOOL_DESCRIPTIONS.get(name, "")
        params = _format_params(t)
        lines.append(f"- {name}({params}): {desc}")
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
    import inspect
    start = time.monotonic()
    fn = tool_map.get(tc.tool)
    if fn is None:
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output={}, error=f"unknown tool: {tc.tool}", duration_ms=0)
    try:
        sig = inspect.signature(fn.func if hasattr(fn, "func") else fn)
        kwargs = dict(tc.args)
        if "repo_path" in sig.parameters:
            kwargs["repo_path"] = prep.repo_path
        if "detected_package_manager" in sig.parameters:
            kwargs["detected_package_manager"] = prep.detected_package_manager
        output = await fn.ainvoke(kwargs) if hasattr(fn, "ainvoke") else await fn(**kwargs)
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output=output if isinstance(output, dict) else {"result": output},
                          error=None, duration_ms=int((time.monotonic() - start) * 1000))
    except Exception as exc:
        logger.warning("tool %s failed: %s", tc.tool, exc)
        return ToolResult(id=str(uuid.uuid4()), tool=tc.tool, args=tc.args,
                          output={}, error=str(exc),
                          duration_ms=int((time.monotonic() - start) * 1000))


def _feedback_result(feedback: str) -> ToolResult:
    """Wrap critic feedback as a tool result so the agent sees it next iteration."""
    return ToolResult(
        id=str(uuid.uuid4()), tool="verification_feedback", args={},
        output={"feedback": feedback}, error=None, duration_ms=0,
    )


async def _react_loop(
    dispatch: AgentDispatch,
    prep: PrepResult,
    tools: list,
    system_prompt: str,
) -> tuple[EvidenceBundle, list[str], int]:
    tool_map = {(getattr(t, "name", None) or getattr(t, "__name__", repr(t))): t for t in tools}
    tool_results: list[ToolResult] = []
    decision: DomainAgentDecision | None = None
    confidence = 0.0
    note: str | None = None

    structured = _llm.with_structured_output(DomainAgentDecision, method="function_calling")

    for iteration in range(_MAX_ITERATIONS):
        react_iterations = iteration + 1
        prompt = (
            f"Tool results so far:\n{_format_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        system = textwrap.dedent(system_prompt).strip().format(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages=_format_packages(dispatch.packages_to_focus, prep.dependency_graph),
            context=prep.discovery_summary[:500],
            tool_descriptions=_format_tools(tools),
            max_iter=_MAX_ITERATIONS,
        )
        try:
            decision = await structured.ainvoke([
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ])
        except Exception as exc:
            logger.warning("structured decision failed, retrying: %s", exc)
            continue

        last = iteration == _MAX_ITERATIONS - 1
        if decision.finalize or last:
            if not decision.findings:
                confidence = decision.confidence
                break
            installed = resolve_installed_versions(
                [f.dep_name for f in decision.findings], prep.dependency_graph
            )
            try:
                verdict = await critique_findings(dispatch, decision.findings, installed)
            except Exception as exc:
                logger.warning("critique_findings failed, accepting draft: %s", exc)
                confidence = decision.confidence
                break
            if verdict.ok:
                confidence = verdict.calibrated_confidence
                break
            if last:
                confidence = verdict.calibrated_confidence
                note = verdict.feedback
                break
            tool_results.append(_feedback_result(verdict.feedback))
            continue

        if decision.tool_calls:
            new_results = await asyncio.gather(
                *[_run_tool(tc, tool_map, prep) for tc in decision.tool_calls]
            )
            tool_results.extend(new_results)

    bundle = EvidenceBundle(
        domain=dispatch.domain,
        hypothesis=dispatch.hypothesis,
        packages_to_focus=dispatch.packages_to_focus,
        findings=decision.findings if decision else [],
        summary=decision.summary if decision else "No results.",
        confidence=confidence,
        verification_note=note,
    )
    return bundle, [tr.tool for tr in tool_results], react_iterations


class BaseAgent(ABC):
    agent_type: ClassVar[str]
    description: ClassVar[str]
    system_prompt: ClassVar[str]

    @abstractmethod
    def _agent_tools(self) -> list: ...

    def get_tools(self, prep: PrepResult) -> list:
        tools = list(self._agent_tools())
        if prep.vector_store_id:
            tools.append(make_search_code_tool(prep.vector_store_id))
        return tools

    async def run(self, dispatch: AgentDispatch, prep: PrepResult) -> tuple[EvidenceBundle, list[str], int]:
        return await _react_loop(dispatch, prep, self.get_tools(prep), self.system_prompt)
