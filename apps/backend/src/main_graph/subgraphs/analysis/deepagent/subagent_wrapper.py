"""Builds one CompiledSubAgent per registered agent_type (spec D2).

Each subagent's runnable is a one-node graph that reuses today's
agent_class().run() unchanged. The root deep agent communicates a task as
free text (deepagents' task() tool has no way to pass a typed AgentDispatch),
so the node's first step is a small structured-output call converting that
text back into an AgentDispatch -- everything after that is identical to
domain_agent.py today.
"""

from __future__ import annotations

import operator
from typing import Annotated, cast

from deepagents import CompiledSubAgent
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.deepagent.coverage import WHOLE_TREE_AGENT_TYPES
from src.main_graph.subgraphs.analysis.deepagent.specialist_runner import run_specialist
from src.models.results import AgentDispatch
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)


class _SubagentState(TypedDict):
    messages: list
    job_id: str
    prep_result_id: str
    agent_calls: Annotated[list[dict], operator.add]
    bundle_ids: Annotated[list[str], operator.add]


async def _extract_dispatch(description: str, agent_type: str) -> AgentDispatch:
    """Turn the root agent's free-text task() description into a typed
    AgentDispatch, so agent_class().run() sees exactly what it sees today."""
    structured = _llm.with_structured_output(AgentDispatch, method="function_calling")
    dispatch = cast(
        AgentDispatch,
        await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract a dependency-analysis dispatch from this task "
                        f"description. Set agent_type to exactly '{agent_type}'."
                    ),
                },
                {"role": "user", "content": description},
            ]
        ),
    )
    return dispatch.model_copy(update={"agent_type": agent_type})


def _existing_bundle_id(agent_calls: list[dict], agent_type: str) -> str | None:
    """D8: whole-tree agents (vulnerability_agent, license_agent) should run
    at most once per job. Not concurrency-safe: `agent_calls` is a snapshot
    of `runtime.state` taken by deepagents' ToolNode BEFORE any sibling
    task() call in the same root turn executes (verified against
    langgraph.prebuilt.tool_node.ToolNode._afunc, which builds every
    ToolRuntime -- and therefore every `state` snapshot -- from the same
    frozen node input before asyncio.gather runs the calls). If the root deep
    agent ever dispatches the SAME whole-tree agent_type twice in one turn
    (now possible after the Finding-1 fix that lets parallel task() calls
    survive at all), both calls see this identical empty/stale snapshot and
    both run, producing a duplicate bundle_id/agent_call for one job.

    Accepted as a residual risk rather than fixed here: closing it requires
    state shared ACROSS the concurrent task() invocations (e.g. a
    per-job/agent_type lock+cache outside graph state), since each task()
    call gets an independently-copied state dict and cannot observe its
    sibling's in-flight write -- a materially larger change than this
    check. Impact is bounded to wasted work, not wrong output: whole-tree
    agents are deterministic (npm audit / SPDX rules), so the duplicate
    bundle's findings are byte-identical to the first and
    save_analysis_result.dedup_findings collapses them before persistence.
    """
    for call in agent_calls:
        if call.get("agent_type") == agent_type:
            return call.get("bundle_id")
    return None


def build_agent_subagent(agent_type: str) -> CompiledSubAgent:
    agent_class = REGISTRY[agent_type]
    description = agent_class.description

    async def _run(state: _SubagentState, config: RunnableConfig) -> dict:
        agent_calls = state.get("agent_calls") or []

        if agent_type in WHOLE_TREE_AGENT_TYPES:
            existing = _existing_bundle_id(agent_calls, agent_type)
            if existing is not None:
                # D8: whole-tree agents run at most once per job.
                return {"messages": [], "bundle_ids": [existing], "agent_calls": []}

        task_description = state["messages"][-1].content
        dispatch = await _extract_dispatch(task_description, agent_type)

        svc = get_services(config)
        prep = await svc["result_dao"].get_prep(state["prep_result_id"])

        bundle_id, record = await run_specialist(agent_type, dispatch, prep, svc)
        return {
            "messages": [],
            "bundle_ids": [bundle_id],
            "agent_calls": [record],
        }

    graph = StateGraph(_SubagentState)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)

    return {
        "name": agent_type,
        "description": description,
        "runnable": graph.compile(),
    }
