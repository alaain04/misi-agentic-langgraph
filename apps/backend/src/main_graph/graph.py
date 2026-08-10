from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import ANALYSIS, PREP, REMEDIATION
from src.main_graph.state import MainState
from src.main_graph.subgraphs.analysis import analysis_subgraph
from src.main_graph.subgraphs.discovery import discovery_subgraph
from src.main_graph.subgraphs.remediation import remediation_subgraph

# report subgraph is disabled for now - not adding value on top of remediation
# output. See src.main_graph.subgraphs.report for the implementation.
# from src.main_graph.subgraphs.report import report_subgraph


def _after_prep(state: MainState) -> str:
    if state.get("discovery_error") or not state.get("prep_result_id"):
        return END
    return ANALYSIS


def _after_analysis(state: MainState) -> str:
    if not state.get("analysis_result_id"):
        return END
    return REMEDIATION


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(PREP, discovery_subgraph)
    builder.add_node(ANALYSIS, analysis_subgraph)
    builder.add_node(REMEDIATION, remediation_subgraph)
    # builder.add_node(REPORT, report_subgraph)

    builder.add_edge(START, PREP)
    builder.add_conditional_edges(PREP, _after_prep, [ANALYSIS, END])
    builder.add_conditional_edges(ANALYSIS, _after_analysis, [REMEDIATION, END])
    builder.add_edge(REMEDIATION, END)
    # builder.add_edge(REMEDIATION, REPORT)
    # builder.add_edge(REPORT, END)

    return builder.compile(checkpointer=InMemorySaver())


main_graph = build_main_graph()
