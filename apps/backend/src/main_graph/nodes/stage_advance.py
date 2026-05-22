"""Stage advance node and router — move to the next execution stage."""

from src.main_graph.constants import EXECUTION_PLANNER, RISK_RANKER, RISK_SCORE
from src.main_graph.state import MainState


def stage_advance(state: MainState) -> dict:
    """Increment the stage counter after a parallel batch completes."""
    return {"current_stage_index": state.get("current_stage_index", 0) + 1}


def stage_router(state: MainState) -> str:
    """Route after a stage completes."""
    idx = state.get("current_stage_index", 0)
    stages = state.get("execution_stages", [])

    if idx < len(stages):
        return EXECUTION_PLANNER

    if not state.get("risk_ranker_done"):
        return RISK_RANKER

    return RISK_SCORE
