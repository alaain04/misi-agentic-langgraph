"""Main graph — ReAct conductor loop."""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    CONDUCTOR,
    HITL_GATE,
    PREP,
    REPORT_BUILDER,
    TOOL_RUNNER,
)
from src.main_graph.nodes.conductor import conductor
from src.main_graph.nodes.hitl_gate import hitl_gate
from src.main_graph.nodes.report_builder import report_builder
from src.main_graph.nodes.tool_runner import tool_runner
from src.main_graph.state import MainState
from src.main_graph.subgraphs.discovery import discovery_subgraph


def _after_prep(state: MainState) -> str:
    if state.get("discovery_error"):
        return END
    return CONDUCTOR


def _after_conductor(state: MainState) -> str:
    decision = state.get("conductor_decision")
    if decision is None:
        return REPORT_BUILDER
    if decision.finalize:
        return REPORT_BUILDER if state.get("autopilot") else HITL_GATE
    if decision.ask_user or decision.checkpoint_message:
        return HITL_GATE
    if decision.tool_calls:
        return TOOL_RUNNER
    return REPORT_BUILDER


def _after_hitl(state: MainState) -> str:
    decision = state.get("conductor_decision")
    if decision and decision.finalize:
        return REPORT_BUILDER
    return CONDUCTOR


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(PREP, discovery_subgraph)
    builder.add_node(CONDUCTOR, conductor)
    builder.add_node(TOOL_RUNNER, tool_runner)
    builder.add_node(HITL_GATE, hitl_gate)
    builder.add_node(REPORT_BUILDER, report_builder)

    builder.add_edge(START, PREP)
    builder.add_conditional_edges(PREP, _after_prep, [CONDUCTOR, END])
    builder.add_conditional_edges(CONDUCTOR, _after_conductor, [TOOL_RUNNER, HITL_GATE, REPORT_BUILDER])
    builder.add_edge(TOOL_RUNNER, CONDUCTOR)
    builder.add_conditional_edges(HITL_GATE, _after_hitl, [CONDUCTOR, REPORT_BUILDER])
    builder.add_edge(REPORT_BUILDER, END)

    return builder.compile(checkpointer=InMemorySaver())


main_graph = build_main_graph()
