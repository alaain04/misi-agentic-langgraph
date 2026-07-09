"""Conductor node — ReAct loop brain."""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from src.main_graph.state import MainState
import src.main_graph.tools.npm_cli  # noqa: F401 — trigger registration
import src.main_graph.tools.package_files  # noqa: F401
import src.main_graph.tools.external_api  # noqa: F401
from src.main_graph.tools.registry import TOOL_DESCRIPTIONS
from src.models.conductor import ConductorDecision
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 10
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are a dependency risk investigator. You run tools to investigate a Node.js project and accumulate findings.

Each iteration you MUST output a ConductorDecision with exactly one primary action:
1. finalize=true — you have enough findings to write the report (highest priority)
2. ask_user or checkpoint_message set — you need user input before continuing
3. tool_calls non-empty — run these tools in parallel and observe results next iteration

Rules:
- Never repeat a tool call with identical arguments.
- Emit FindingNote entries for every risk you observe in tool results.
- In autopilot mode, never set ask_user or checkpoint_message.
- After 10 iterations, you MUST finalize regardless of confidence.

Available tools:
{tool_descriptions}
"""


def _format_tool_descriptions() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in TOOL_DESCRIPTIONS.items())


def _format_tool_results(tool_results: list) -> str:
    if not tool_results:
        return "No tool results yet."
    parts = []
    for tr in tool_results[-20:]:  # show last 20 to avoid context overflow
        if tr.error:
            result_str = f"ERROR: {tr.error}"
        else:
            output_json = json.dumps(tr.output, indent=2)
            result_str = output_json[:2000] + (" ... [truncated]" if len(output_json) > 2000 else "")
        parts.append(f"[{tr.id}] {tr.tool}({tr.args}) → {result_str}")
    return "\n\n".join(parts)


def _format_findings(findings: list) -> str:
    if not findings:
        return "No findings yet."
    return "\n".join(
        f"- [{f.severity.upper()}] {f.dep_name}: {f.description}"
        for f in findings
    )


def _format_messages(messages: list) -> str:
    if not messages:
        return "No conversation history."
    parts = []
    for m in messages:
        role = "assistant" if isinstance(m, AIMessage) else "user"
        parts.append(f"[{role}]: {m.content}")
    return "\n".join(parts)


async def conductor(state: MainState, config: RunnableConfig) -> dict:
    iteration = (state.get("conductor_iteration") or 0) + 1

    user_prompt = (
        f"Concern: {state['concern']}\n\n"
        f"Project context:\n{state.get('project_context', '')}\n\n"
        f"Package manager: {state.get('detected_package_manager', 'unknown')}\n\n"
        f"Tool results so far:\n{_format_tool_results(state.get('tool_results') or [])}\n\n"
        f"Findings accumulated:\n{_format_findings(state.get('findings') or [])}\n\n"
        f"Conversation history:\n{_format_messages(state.get('messages') or [])}\n\n"
        f"Iteration: {iteration}/{_MAX_ITERATIONS}"
    )
    if state.get("autopilot"):
        user_prompt += "\n\nAUTOPILOT MODE: do not set ask_user or checkpoint_message."

    system = _SYSTEM.format(tool_descriptions=_format_tool_descriptions())

    structured_llm = _llm.with_structured_output(ConductorDecision, method="function_calling")
    decision: ConductorDecision = await structured_llm.ainvoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ])

    # Enforce max iteration guard
    if iteration >= _MAX_ITERATIONS:
        decision = decision.model_copy(update={"finalize": True})

    # Enforce autopilot
    if state.get("autopilot"):
        decision = decision.model_copy(update={"ask_user": None, "checkpoint_message": None})

    logger.info(
        "conductor: iteration=%d finalize=%s tools=%d findings=%d ask_user=%s",
        iteration, decision.finalize, len(decision.tool_calls), len(decision.findings),
        bool(decision.ask_user),
    )

    return {
        "conductor_iteration": iteration,
        "conductor_decision": decision,
        "findings": decision.findings,  # accumulated via operator.add
    }
