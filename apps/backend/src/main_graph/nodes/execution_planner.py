"""Execution planner node — builds per-dep fan-out Stage 1 entries once."""

from src.main_graph.state import MainState

_SBOM_LEVEL = frozenset({"vulnerabilities", "license_compliance"})
_PER_DEP = frozenset({"registry", "repo", "runtime"})


def execution_planner(state: MainState) -> dict:
    """Compute Stage 1 execution entries. Runs once; subsequent calls are no-ops."""
    if state.get("execution_stages") is not None:
        return {}

    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else list(plan_obj)
    )
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )

    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    stage1: list[dict] = []

    for sg in subgraphs:
        if sg in _SBOM_LEVEL:
            stage1.append({"subgraph": sg, "dep_name": None})

    for sg in subgraphs:
        if sg in _PER_DEP:
            for dep in dep_scope:
                stage1.append({"subgraph": sg, "dep_name": dep})

    return {
        "execution_stages": [stage1],
        "current_stage_index": 0,
        "dep_filter": dep_filter,
    }
