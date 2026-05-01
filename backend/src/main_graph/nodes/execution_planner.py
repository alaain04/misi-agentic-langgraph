"""Execution planner node — resolves dependency-aware execution stages once."""

from src.main_graph.state import MainState
from src.main_graph.subgraph_registry import SUBGRAPH_DEPENDENCIES
from src.main_graph.utils.dependency_resolver import resolve_execution_stages


def execution_planner(state: MainState) -> dict:
    """Compute execution stages from the approved plan (runs once per job)."""
    if state.get("execution_stages") is not None:
        return {}

    plan = state.get("plan", [])
    stages = resolve_execution_stages(plan=plan, deps=SUBGRAPH_DEPENDENCIES)
    return {"execution_stages": stages, "current_stage_index": 0}
