from __future__ import annotations

from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)
from src.models.conductor import FindingNote
from src.models.remediation import FindingSummary, RemediationTarget
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
    summaries: dict[str, dict[str, FindingSummary]] = {}
    for finding in survivors:
        for anchor in _anchors(dependency_graph, finding.dep_name):
            grouped.setdefault(anchor, set()).add(finding.dep_name)
            summaries.setdefault(anchor, {})[finding.dep_name] = FindingSummary(
                dep_name=finding.dep_name,
                severity=finding.severity,
                description=finding.description,
            )

    return [
        RemediationTarget(
            target_dep=dep,
            addresses=sorted(addressed),
            finding_summaries=[
                summaries[dep][name] for name in sorted(addressed)
            ],
            current_range=direct.get(dep),
        )
        for dep, addressed in sorted(grouped.items())
    ]
