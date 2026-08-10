from __future__ import annotations

from src.main_graph.subgraphs.remediation.plan import _enforce_tier


def _bump_plan(dep: str, tier: str, to_range: str) -> dict:
    return {
        "target_dep": dep,
        "tier_hint": tier,
        "migration_guide": "",
        "tasks": [
            {
                "kind": "bump",
                "rationale": "clean upgrade",
                "to_range": to_range,
                "files": [],
                "replacement_dep": None,
                "replacement_range": None,
            }
        ],
        "requires": [],
    }


def test_enforce_tier_replaces_bump_task_on_r3_plan():
    """Regression (job 6a7773a7576d0efd7796aa8c, `matcha`): the planner
    stamped tier_hint r3 on a plan whose only task was a bump to the
    installed version. An r3 package has no same-package fix, so the bump
    cannot survive."""
    plans = {"matcha": _bump_plan("matcha", "r3", "^0.7.0")}
    targets = {"matcha": {"target_dep": "matcha", "tier": "r3"}}

    _enforce_tier(plans, targets)

    kinds = [t["kind"] for t in plans["matcha"]["tasks"]]
    assert kinds == ["replace"]
    assert plans["matcha"]["tier_hint"] == "r3"
    assert "no replacement dependency was named" in (
        plans["matcha"]["tasks"][0]["rationale"].lower()
    )


def test_enforce_tier_keeps_existing_replace_task_and_drops_bump():
    plans = {
        "old-dep": {
            "target_dep": "old-dep",
            "tier_hint": "r3",
            "migration_guide": "",
            "tasks": [
                {"kind": "bump", "rationale": "b", "to_range": "^1.0.0"},
                {
                    "kind": "replace",
                    "rationale": "unmaintained",
                    "replacement_dep": "new-dep",
                    "replacement_range": "^2.0.0",
                },
            ],
            "requires": [],
        }
    }
    targets = {"old-dep": {"target_dep": "old-dep", "tier": "r3"}}

    _enforce_tier(plans, targets)

    tasks = plans["old-dep"]["tasks"]
    assert [t["kind"] for t in tasks] == ["replace"]
    assert tasks[0]["replacement_dep"] == "new-dep"


def test_enforce_tier_leaves_r1_and_r2_plans_untouched():
    plans = {
        "lodash": _bump_plan("lodash", "r1", "^4.17.21"),
        "express": _bump_plan("express", "r2", "^5.0.0"),
    }
    before = {dep: [dict(t) for t in p["tasks"]] for dep, p in plans.items()}
    targets = {
        "lodash": {"target_dep": "lodash", "tier": "r1"},
        "express": {"target_dep": "express", "tier": "r2"},
    }

    _enforce_tier(plans, targets)

    assert plans["lodash"]["tasks"] == before["lodash"]
    assert plans["express"]["tasks"] == before["express"]


def test_enforce_tier_ignores_target_with_no_tier():
    plans = {"lodash": _bump_plan("lodash", "r1", "^4.17.21")}
    targets = {"lodash": {"target_dep": "lodash"}}

    _enforce_tier(plans, targets)

    assert [t["kind"] for t in plans["lodash"]["tasks"]] == ["bump"]
