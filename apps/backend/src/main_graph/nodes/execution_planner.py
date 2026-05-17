"""Execution planner node — resolves dependency-aware execution stages once."""

from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_DEPENDENCIES
from src.main_graph.utils.dependency_resolver import resolve_execution_stages


def _expand_with_dependencies(plan: list[str], deps: dict[str, list[str]]) -> list[str]:
    plan_set = set(plan)
    expanded = plan_set.copy()
    queue = list(plan)
    while queue:
        sg = queue.pop()
        for dep in deps.get(sg, []):
            if dep not in expanded:
                expanded.add(dep)
                queue.append(dep)
    return list(plan) + list(expanded - plan_set)


def execution_planner(state: MainState) -> dict:
    """Compute execution stages from the approved plan (runs once per job)."""
    if state.get("execution_stages") is not None:
        return {}

    plan = state.get("plan", [])
    expanded = _expand_with_dependencies(plan, SUBGRAPH_DEPENDENCIES)
    stages = resolve_execution_stages(plan=expanded, deps=SUBGRAPH_DEPENDENCIES)
    return {"execution_stages": stages, "current_stage_index": 0}
