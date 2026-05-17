"""Topological sort of ingestion subgraphs into parallel execution stages."""

from collections import deque


def resolve_execution_stages(
    plan: list[str],
    deps: dict[str, list[str]],
) -> list[list[str]]:
    """Return parallel execution stages for the selected subgraphs.

    Uses Kahn's algorithm. Each inner list can run concurrently; lists must
    execute in order. Dependencies on subgraphs not in the plan are ignored.

    Raises ValueError on cycles.
    """
    plan_set = set(plan)

    # Restrict deps to nodes that are actually in the plan
    in_plan_deps: dict[str, list[str]] = {
        name: [d for d in deps.get(name, []) if d in plan_set] for name in plan
    }

    in_degree: dict[str, int] = {name: len(in_plan_deps[name]) for name in plan}
    dependents: dict[str, list[str]] = {name: [] for name in plan}
    for name, prerequisites in in_plan_deps.items():
        for prereq in prerequisites:
            dependents[prereq].append(name)

    queue: deque[str] = deque(n for n in plan if in_degree[n] == 0)
    stages: list[list[str]] = []
    visited = 0

    while queue:
        stage = list(queue)
        queue.clear()
        stages.append(stage)
        visited += len(stage)
        next_wave: list[str] = []
        for node in stage:
            for dependent in dependents[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_wave.append(dependent)
        queue.extend(next_wave)

    if visited != len(plan):
        raise ValueError(f"Cycle detected among ingestion subgraphs: {plan}")

    return stages
