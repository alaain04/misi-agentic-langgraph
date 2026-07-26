"""Confirms deepagents.CompiledSubAgent state updates merge into the root
deep agent's state via ordinary LangGraph reducers (not just a summarized
ToolMessage). This is the load-bearing mechanism for D4 in
docs/superpowers/specs/2026-07-26-analysis-subgraph-deepagent-swap.md --
verified here against the pinned deepagents version inside this project,
not assumed."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Sequence

import pytest
from deepagents import CompiledSubAgent, create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.main_graph.subgraphs.analysis.deepagent.state import AnalysisDeepAgentState


class _ScriptedToolCallingChatModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel does not implement bind_tools (raises
    NotImplementedError), but deepagents calls model.bind_tools(...)
    internally. Override it as a no-op so the fake just returns its
    scripted responses regardless of the tool schema passed in."""

    def bind_tools(
        self, tools: Sequence[Any], **kwargs: Any
    ) -> "_ScriptedToolCallingChatModel":
        return self


class _EchoSubState(TypedDict):
    messages: list
    bundle_ids: Annotated[list[str], operator.add]


def _echo_node(state: _EchoSubState) -> dict:
    return {"messages": [AIMessage(content="done")], "bundle_ids": ["fake-bundle-1"]}


def _build_echo_subagent() -> CompiledSubAgent:
    graph = StateGraph(_EchoSubState)
    graph.add_node("echo", _echo_node)
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    return {
        "name": "echo_agent",
        "description": "Echoes back a fixed bundle id.",
        "runnable": graph.compile(),
    }


@pytest.mark.asyncio
async def test_subagent_state_update_merges_into_root_state():
    fake_model = _ScriptedToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "run echo", "subagent_type": "echo_agent"},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content="all done"),
        ]
    )
    agent = create_deep_agent(
        model=fake_model,
        subagents=[_build_echo_subagent()],
        state_schema=AnalysisDeepAgentState,
    )
    result = await agent.ainvoke(
        {
            "messages": [HumanMessage(content="go")],
            "job_id": "job-1",
            "prep_result_id": "prep-1",
            "bundle_ids": [],
            "agent_calls": [],
        }
    )
    assert result["bundle_ids"] == ["fake-bundle-1"]
