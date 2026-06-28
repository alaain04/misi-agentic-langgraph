"""Main graph — 8-node cognitive investigation pipeline."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    DISCOVERY,
    EVIDENCE_COLLECTOR,
    EVIDENCE_CORRELATOR,
    FINDING_REVIEWER,
    INVESTIGATION_PLANNER,
    REPORT_BUILDER,
    SKILL_DISPATCHER,
    SKILL_EXECUTOR,
)
from src.main_graph.nodes.evidence_collector import evidence_collector
from src.main_graph.nodes.evidence_correlator import evidence_correlator
from src.main_graph.nodes.finding_reviewer import finding_reviewer
from src.main_graph.nodes.investigation_planner import investigation_planner
from src.main_graph.nodes.report_builder import report_builder
from src.main_graph.nodes.skill_dispatcher import skill_dispatcher
from src.main_graph.nodes.skill_executor import skill_executor
from src.main_graph.state import MainState
from src.main_graph.subgraphs.discovery import discovery_subgraph

_MAX_REVIEW_ITERATIONS = 2


def _reviewer_route(state: MainState) -> str:
    if state.get("reviewer_feedback") and (state.get("review_iterations") or 0) < _MAX_REVIEW_ITERATIONS:
        return EVIDENCE_CORRELATOR
    return REPORT_BUILDER


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(DISCOVERY, discovery_subgraph)
    builder.add_node(INVESTIGATION_PLANNER, investigation_planner)
    builder.add_node(SKILL_DISPATCHER, skill_dispatcher)
    builder.add_node(SKILL_EXECUTOR, skill_executor)
    builder.add_node(EVIDENCE_COLLECTOR, evidence_collector)
    builder.add_node(EVIDENCE_CORRELATOR, evidence_correlator)
    builder.add_node(FINDING_REVIEWER, finding_reviewer)
    builder.add_node(REPORT_BUILDER, report_builder)

    builder.add_edge(START, DISCOVERY)
    builder.add_edge(DISCOVERY, INVESTIGATION_PLANNER)
    builder.add_edge(INVESTIGATION_PLANNER, SKILL_DISPATCHER)
    # skill_dispatcher returns list[Send] — LangGraph handles fan-out internally
    builder.add_edge(SKILL_EXECUTOR, EVIDENCE_COLLECTOR)
    builder.add_edge(EVIDENCE_COLLECTOR, EVIDENCE_CORRELATOR)
    builder.add_edge(EVIDENCE_CORRELATOR, FINDING_REVIEWER)
    builder.add_conditional_edges(FINDING_REVIEWER, _reviewer_route, [EVIDENCE_CORRELATOR, REPORT_BUILDER])
    builder.add_edge(REPORT_BUILDER, END)

    return builder.compile(
        checkpointer=InMemorySaver(),
        interrupt_before=[INVESTIGATION_PLANNER],
    )


main_graph = build_main_graph()
