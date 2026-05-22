"""Main graph — composes all subgraphs into the full analysis pipeline."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.constants import (
    CROSS_ANALYZER,
    DISCOVERY,
    EXECUTE_PLAN,
    EXECUTION_PLANNER,
    ORCHESTRATOR,
    RECOMMENDATION,
    REPORT_REVIEWER,
    RISK_RANKER,
    RISK_SCORE,
    STAGE_ADVANCE,
)
from src.main_graph.nodes import (
    execute_plan,
    execution_planner,
    orchestrator,
    recommendation,
    risk_ranker,
    risk_ranker_router,
    risk_score,
    stage_advance,
    stage_router,
    task_dispatcher,
)
from src.main_graph.state import MainState
from src.main_graph.subgraphs import (
    cross_analyzer_subgraph,
    discovery_subgraph,
    report_reviewer_subgraph,
)

_checkpointer = InMemorySaver()

_MAX_REVIEW_ITERATIONS = 2


def _review_router(state: MainState) -> str:
    if (
        state.get("review_approved")
        or state.get("review_iterations", 0) >= _MAX_REVIEW_ITERATIONS
    ):
        return END
    return CROSS_ANALYZER


def build_main_graph():
    builder = StateGraph(MainState)

    builder.add_node(DISCOVERY, discovery_subgraph)
    builder.add_node(ORCHESTRATOR, orchestrator)
    builder.add_node(EXECUTION_PLANNER, execution_planner)
    builder.add_node(EXECUTE_PLAN, execute_plan)
    builder.add_node(STAGE_ADVANCE, stage_advance)
    builder.add_node(RISK_RANKER, risk_ranker)
    builder.add_node(RISK_SCORE, risk_score)
    builder.add_node(RECOMMENDATION, recommendation)
    builder.add_node(CROSS_ANALYZER, cross_analyzer_subgraph)
    builder.add_node(REPORT_REVIEWER, report_reviewer_subgraph)

    builder.add_edge(START, DISCOVERY)
    builder.add_edge(DISCOVERY, ORCHESTRATOR)
    builder.add_edge(ORCHESTRATOR, EXECUTION_PLANNER)
    builder.add_conditional_edges(EXECUTION_PLANNER, task_dispatcher, [EXECUTE_PLAN])
    builder.add_edge(EXECUTE_PLAN, STAGE_ADVANCE)
    builder.add_conditional_edges(
        STAGE_ADVANCE, stage_router, [EXECUTION_PLANNER, RISK_RANKER, RISK_SCORE]
    )
    builder.add_conditional_edges(
        RISK_RANKER, risk_ranker_router, [EXECUTION_PLANNER, RISK_SCORE]
    )
    builder.add_edge(RISK_SCORE, RECOMMENDATION)
    builder.add_edge(RECOMMENDATION, CROSS_ANALYZER)
    builder.add_edge(CROSS_ANALYZER, REPORT_REVIEWER)
    builder.add_conditional_edges(
        REPORT_REVIEWER, _review_router, [CROSS_ANALYZER, END]
    )

    return builder.compile(checkpointer=_checkpointer)


main_graph = build_main_graph()
