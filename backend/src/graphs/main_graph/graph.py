"""Main graph — composes all subgraphs into the full analysis pipeline."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.graphs.main_graph.constants import (
    ORCHESTRATOR,
    PROJECT_DISCOVERY,
    RECOMMENDER,
    REVIEWER,
    RUN_SUBGRAPH,
    SUMMARIZER,
)
from src.graphs.main_graph.nodes import (
    orchestrator,
    recommender,
    reviewer,
    run_subgraph,
    summarizer,
    task_dispatcher,
)
from src.graphs.main_graph.state import MainState
from src.graphs.project_discovery import project_discovery_subgraph

_checkpointer = InMemorySaver()


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(PROJECT_DISCOVERY, project_discovery_subgraph)
    builder.add_node(ORCHESTRATOR, orchestrator)
    builder.add_node(RUN_SUBGRAPH, run_subgraph)
    builder.add_node(SUMMARIZER, summarizer)
    builder.add_node(REVIEWER, reviewer)
    builder.add_node(RECOMMENDER, recommender)

    builder.add_edge(START, PROJECT_DISCOVERY)
    builder.add_edge(PROJECT_DISCOVERY, ORCHESTRATOR)
    builder.add_conditional_edges(ORCHESTRATOR, task_dispatcher, [RUN_SUBGRAPH])
    builder.add_edge(RUN_SUBGRAPH, SUMMARIZER)
    builder.add_edge(SUMMARIZER, REVIEWER)
    builder.add_edge(REVIEWER, RECOMMENDER)
    builder.add_edge(RECOMMENDER, END)

    return builder.compile(checkpointer=_checkpointer)


main_graph = build_main_graph()
