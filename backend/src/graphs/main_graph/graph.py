"""Main graph — composes all subgraphs into the full analysis pipeline."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.graphs.main_graph.constants import (
    FINAL_REPORT,
    PLAN_REVIEW,
    PLANNER,
    PROJECT_DISCOVERY,
    RUN_SUBGRAPH,
)
from src.graphs.main_graph.nodes import (
    final_report,
    plan_review,
    planner,
    run_subgraph,
    task_dispatcher,
)
from src.graphs.main_graph.state import MainState
from src.graphs.project_discovery import project_discovery_subgraph

_checkpointer = InMemorySaver()


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(PROJECT_DISCOVERY, project_discovery_subgraph)
    builder.add_node(PLANNER, planner)
    builder.add_node(PLAN_REVIEW, plan_review)
    builder.add_node(RUN_SUBGRAPH, run_subgraph)
    builder.add_node(FINAL_REPORT, final_report)

    builder.add_edge(START, PROJECT_DISCOVERY)
    builder.add_edge(PROJECT_DISCOVERY, PLANNER)
    builder.add_edge(PLANNER, PLAN_REVIEW)
    builder.add_conditional_edges(PLAN_REVIEW, task_dispatcher, [RUN_SUBGRAPH])
    builder.add_edge(RUN_SUBGRAPH, FINAL_REPORT)
    builder.add_edge(FINAL_REPORT, END)

    return builder.compile(checkpointer=_checkpointer)


main_graph = build_main_graph()
