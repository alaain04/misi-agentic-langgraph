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
from datetime import UTC, datetime
from typing import Annotated, cast

from deepagents import CompiledSubAgent
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.main_graph.config import get_services
from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY
from src.main_graph.subgraphs.analysis.deepagent.coverage import WHOLE_TREE_AGENT_TYPES
from src.models.results import AgentCallRecord, AgentDispatch
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

        started_at = datetime.now(UTC).isoformat()
        bundle, tools_used, react_iterations = await agent_class().run(
            dispatch, prep, svc["container"], cache=svc.get("input_cache")
        )
        finished_at = datetime.now(UTC).isoformat()

        bundle_id = await svc["result_dao"].save_bundle(bundle)

        record = AgentCallRecord(
            conductor_iteration=0,  # no conductor-iteration concept anymore;
            # frontend rendering of this field is explicitly out of scope
            # (see spec "Out of scope").
            agent_type=agent_type,
            domain=dispatch.domain,
            tools_used=tools_used,
            react_iterations=react_iterations,
            started_at=started_at,
            finished_at=finished_at,
            bundle_id=bundle_id,
        )
        return {
            "messages": [],
            "bundle_ids": [bundle_id],
            "agent_calls": [record.model_dump()],
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
