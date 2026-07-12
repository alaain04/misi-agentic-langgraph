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


async def _react_loop(
    dispatch: AgentDispatch,
    prep: PrepResult,
    tools: list,
    system_prompt: str,
) -> EvidenceBundle:
    tool_map = {(getattr(t, "name", None) or getattr(t, "__name__", repr(t))): t for t in tools}
    tool_results: list[ToolResult] = []
    decision: DomainAgentDecision | None = None

    structured = _llm.with_structured_output(DomainAgentDecision, method="function_calling")

    for iteration in range(_MAX_ITERATIONS):
        prompt = (
            f"Tool results so far:\n{_format_results(tool_results)}\n\n"
            f"Iteration: {iteration + 1}/{_MAX_ITERATIONS}"
        )
        system = textwrap.dedent(system_prompt).strip().format(
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
        packages_to_focus=dispatch.packages_to_focus,
        findings=decision.findings if decision else [],
        summary=decision.summary if decision else "No results.",
        confidence=decision.confidence if decision else 0.0,
    )


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

    async def run(self, dispatch: AgentDispatch, prep: PrepResult) -> EvidenceBundle:
        return await _react_loop(dispatch, prep, self.get_tools(prep), self.system_prompt)
