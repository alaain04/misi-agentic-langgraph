"""Simplified C1/C2/C3 conflict rules over a (project, dependency) license
pair, per the term model in Liu et al. (arXiv:2401.10636):

- C1: project's license CAN something the dependency's license marks CANNOT.
- C2: dependency's license MUSTs an obligation the project doesn't fulfill.
- C3: copyleft contagion — dependency requires derivative works to carry the
  same license, and the project's license doesn't preserve that.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.main_graph.subgraphs.analysis.agents.license_data import LicenseEntry


@dataclass(frozen=True)
class Conflict:
    rule: str  # "C1" | "C2" | "C3"
    severity: str  # "medium" | "low" | "high"
    detail: str


_C1_FIELDS = {
    "sublicense": "sublicensing",
    "commercial_use": "commercial use",
}
_C2_FIELDS = {
    "include_notice": "retain the original copyright/license notice",
    "disclose_source": "disclose the source of modifications",
    "state_changes": "state what changes were made to the code",
}
_C3_CATEGORIES = ("strong_copyleft", "network_copyleft")


def _check_c1(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> list[Conflict]:
    conflicts = []
    for field, label in _C1_FIELDS.items():
        if getattr(project, field) == "can" and getattr(dep, field) == "cannot":
            conflicts.append(
                Conflict(
                    rule="C1",
                    severity="medium",
                    detail=(
                        f"Project license {project_id} permits {label}, but "
                        f"dependency license {dep_id} does not grant this right."
                    ),
                )
            )
    return conflicts


def _check_c2(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> list[Conflict]:
    conflicts = []
    for field, label in _C2_FIELDS.items():
        if getattr(dep, field) == "must" and getattr(project, field) != "must":
            conflicts.append(
                Conflict(
                    rule="C2",
                    severity="low",
                    detail=(
                        f"Dependency license {dep_id} requires the project to {label}, "
                        f"but project license {project_id} does not declare this "
                        f"obligation as fulfilled."
                    ),
                )
            )
    return conflicts


def _check_c3(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> Conflict | None:
    if dep.same_license != "must" or dep.category not in _C3_CATEGORIES:
        return None
    if project_id == dep_id or project.category in _C3_CATEGORIES:
        return None
    return Conflict(
        rule="C3",
        severity="high",
        detail=(
            f"Dependency license {dep_id} is copyleft and requires derivative works "
            f"to remain under the same terms, but project license {project_id} does "
            f"not preserve this — copyleft contagion risk."
        ),
    )


def check_conflicts(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> list[Conflict]:
    conflicts = _check_c1(project_id, project, dep_id, dep)
    conflicts.extend(_check_c2(project_id, project, dep_id, dep))
    c3 = _check_c3(project_id, project, dep_id, dep)
    if c3:
        conflicts.append(c3)
    return conflicts
