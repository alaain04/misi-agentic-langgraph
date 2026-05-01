"""Main graph — composes all subgraphs into the full analysis pipeline."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    DISCOVERY,
    EXECUTE_PLAN,
    ORCHESTRATOR,
    RECOMMENDER,
    REVIEWER,
    SUMMARIZER,
)
from src.main_graph.state import MainState
from src.main_graph.subgraphs import (
    discovery_subgraph,
    orchestrator_subgraph,
    recommender_subgraph,
    reviewer_subgraph,
    summarizer_subgraph,
)

from .nodes import execute_plan, task_dispatcher

_checkpointer = InMemorySaver()


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(DISCOVERY, discovery_subgraph)
    builder.add_node(ORCHESTRATOR, orchestrator_subgraph)
    builder.add_node(EXECUTE_PLAN, execute_plan)
    builder.add_node(SUMMARIZER, summarizer_subgraph)
    builder.add_node(REVIEWER, reviewer_subgraph)
    builder.add_node(RECOMMENDER, recommender_subgraph)

    builder.add_edge(START, DISCOVERY)
    builder.add_edge(DISCOVERY, ORCHESTRATOR)
    builder.add_conditional_edges(ORCHESTRATOR, task_dispatcher, [EXECUTE_PLAN])
    builder.add_edge(EXECUTE_PLAN, SUMMARIZER)
    builder.add_edge(SUMMARIZER, REVIEWER)
    builder.add_edge(REVIEWER, RECOMMENDER)
    builder.add_edge(RECOMMENDER, END)

    return builder.compile(checkpointer=_checkpointer)


main_graph = build_main_graph()
