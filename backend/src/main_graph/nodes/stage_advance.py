"""Stage advance node and router — move to the next execution stage."""

from src.main_graph.constants import CROSS_ANALYZER, EXECUTION_PLANNER
from src.main_graph.state import MainState


def stage_advance(state: MainState) -> dict:
    """Increment the stage counter after a parallel batch of subgraphs completes."""
    return {"current_stage_index": state.get("current_stage_index", 0) + 1}


def stage_router(state: MainState) -> str:
    """Route to the next stage dispatch loop or to the cross-analyzer when done."""
    idx = state.get("current_stage_index", 0)
    stages = state.get("execution_stages", [])
    return EXECUTION_PLANNER if idx < len(stages) else CROSS_ANALYZER
