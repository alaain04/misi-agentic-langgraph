from __future__ import annotations

from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)
from src.models.conductor import FindingNote
from src.models.remediation import RemediationTarget
from src.utils.severity import filter_by_min_severity


def _anchors(graph: dict, dep_name: str) -> list[str]:
    if is_direct(graph, dep_name):
        return [dep_name]
    return direct_dependents(graph, dep_name)


def select_remediation_targets(
    findings: list[FindingNote], dependency_graph: dict, min_severity: str
) -> list[RemediationTarget]:
    """Deterministic: filter by severity, anchor transitives to their direct
    dependent(s), unify findings that share a direct-dep bump.

    Findings with no direct anchor (no lever the user controls) are dropped.
    """
    survivors = filter_by_min_severity(findings, min_severity)
    direct = dependency_graph.get("direct") or {}

    grouped: dict[str, set[str]] = {}
    for finding in survivors:
        for anchor in _anchors(dependency_graph, finding.dep_name):
            grouped.setdefault(anchor, set()).add(finding.dep_name)

    return [
        RemediationTarget(
            target_dep=dep,
            addresses=sorted(addressed),
            current_range=direct.get(dep),
        )
        for dep, addressed in sorted(grouped.items())
    ]
