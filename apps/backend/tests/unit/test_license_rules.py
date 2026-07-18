from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.license_data import LICENSES
from src.main_graph.subgraphs.analysis.agents.license_rules import (
    _check_c1,
    _check_c2,
    _check_c3,
    check_conflicts,
)


def test_c1_no_conflict_when_both_can_sublicense():
    # spec example: project CAN sublicense + Apache-2.0 dependency (CAN sublicense)
    conflicts = _check_c1("MIT", LICENSES["MIT"], "Apache-2.0", LICENSES["Apache-2.0"])
    assert conflicts == []


def test_c1_flags_when_project_can_dependency_cannot():
    conflicts = _check_c1(
        "MIT", LICENSES["MIT"], "GPL-3.0-only", LICENSES["GPL-3.0-only"]
    )
    assert len(conflicts) == 1
    assert conflicts[0].rule == "C1"
    assert conflicts[0].severity == "medium"
    assert "MIT" in conflicts[0].detail
    assert "GPL-3.0-only" in conflicts[0].detail


def test_c2_flags_when_dependency_musts_obligation_project_lacks():
    # spec example: MIT dependency (requires include_notice) + no project license
    conflicts = _check_c2("UNLICENSED", LICENSES["UNLICENSED"], "MIT", LICENSES["MIT"])
    assert any(c.rule == "C2" and c.severity == "low" for c in conflicts)
    include_notice_conflicts = [c for c in conflicts if "notice" in c.detail]
    assert len(include_notice_conflicts) == 1


def test_c2_no_conflict_when_project_already_musts_same_obligation():
    conflicts = _check_c2("MIT", LICENSES["MIT"], "MIT", LICENSES["MIT"])
    assert conflicts == []


def test_c3_flags_copyleft_contagion_gpl_dependency_into_mit_project():
    # spec example: GPL-3.0-only dependency + MIT project -> C3/high
    conflict = _check_c3(
        "MIT", LICENSES["MIT"], "GPL-3.0-only", LICENSES["GPL-3.0-only"]
    )
    assert conflict is not None
    assert conflict.rule == "C3"
    assert conflict.severity == "high"


def test_c3_no_conflict_when_project_is_same_id():
    conflict = _check_c3(
        "GPL-3.0-only",
        LICENSES["GPL-3.0-only"],
        "GPL-3.0-only",
        LICENSES["GPL-3.0-only"],
    )
    assert conflict is None


def test_c3_no_conflict_when_project_itself_copyleft():
    conflict = _check_c3(
        "GPL-3.0-only",
        LICENSES["GPL-3.0-only"],
        "AGPL-3.0-only",
        LICENSES["AGPL-3.0-only"],
    )
    assert conflict is None


def test_c3_no_conflict_when_dependency_not_copyleft():
    conflict = _check_c3("MIT", LICENSES["MIT"], "Apache-2.0", LICENSES["Apache-2.0"])
    assert conflict is None


def test_check_conflicts_returns_all_three_rule_types_for_mit_project_gpl_dependency():
    conflicts = check_conflicts(
        "MIT", LICENSES["MIT"], "GPL-3.0-only", LICENSES["GPL-3.0-only"]
    )
    rules = {c.rule for c in conflicts}
    assert rules == {"C1", "C2", "C3"}
    assert max(c.severity for c in conflicts if c.rule == "C3") == "high"
