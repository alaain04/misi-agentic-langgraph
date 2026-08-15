from __future__ import annotations

from src.main_graph.subgraphs.remediation.plan import (
    _apply_release_digest,
    _enforce_tier,
    _format_targets,
)


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


def _replace_task(replacement_dep: str | None = None) -> dict:
    return {
        "kind": "replace",
        "rationale": "unmaintained",
        "to_range": None,
        "files": [],
        "replacement_dep": replacement_dep,
        "replacement_range": None,
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


def test_enforce_tier_caps_r3_plan_to_one_replace_task():
    plans = {
        "matcha": {
            "target_dep": "matcha",
            "tier_hint": "r3",
            "migration_guide": "",
            "tasks": [
                _replace_task("first-candidate"),
                _replace_task("second-candidate"),
            ],
            "requires": [],
        }
    }
    targets = {"matcha": {"target_dep": "matcha", "tier": "r3"}}

    _enforce_tier(plans, targets)

    tasks = plans["matcha"]["tasks"]
    assert [t["kind"] for t in tasks] == ["replace"]
    assert tasks[0]["replacement_dep"] == "first-candidate"


def test_enforce_tier_drops_stray_replace_task_on_r1_plan():
    plans = {
        "lodash": {
            "target_dep": "lodash",
            "tier_hint": "r1",
            "migration_guide": "",
            "tasks": [
                {
                    "kind": "bump",
                    "rationale": "clean upgrade",
                    "to_range": "^4.17.21",
                },
                _replace_task("some-alternative"),
            ],
            "requires": [],
        }
    }
    targets = {"lodash": {"target_dep": "lodash", "tier": "r1"}}

    _enforce_tier(plans, targets)

    assert [t["kind"] for t in plans["lodash"]["tasks"]] == ["bump"]


def test_enforce_tier_clears_noop_bump_to_range():
    plans = {"lodash": _bump_plan("lodash", "r1", "^4.17.21")}
    targets = {
        "lodash": {
            "target_dep": "lodash",
            "tier": "r1",
            "current_range": "^4.17.21",
        }
    }

    _enforce_tier(plans, targets)

    assert plans["lodash"]["tasks"][0]["to_range"] is None


def test_enforce_tier_keeps_bump_to_range_when_it_differs_from_current():
    plans = {"lodash": _bump_plan("lodash", "r1", "^4.17.21")}
    targets = {
        "lodash": {
            "target_dep": "lodash",
            "tier": "r1",
            "current_range": "^4.15.0",
        }
    }

    _enforce_tier(plans, targets)

    assert plans["lodash"]["tasks"][0]["to_range"] == "^4.17.21"


def test_apply_release_digest_overwrites_guide_and_known_to_range():
    plans = {"lodash": _bump_plan("lodash", "r1", "^4.0.0")}
    investigations = {
        "lodash": {
            "release": {
                "migration_guide": "Follow the official upgrade guide.",
                "to_version": "4.17.21",
            }
        }
    }

    _apply_release_digest(plans, investigations)

    assert plans["lodash"]["migration_guide"] == "Follow the official upgrade guide."
    assert plans["lodash"]["tasks"][0]["to_range"] == "4.17.21"


def test_apply_release_digest_leaves_to_range_when_version_unknown():
    plans = {"lodash": _bump_plan("lodash", "r1", "^4.99.0")}
    investigations = {
        "lodash": {"release": {"migration_guide": "", "to_version": None}}
    }

    _apply_release_digest(plans, investigations)

    assert plans["lodash"]["migration_guide"] == ""
    assert plans["lodash"]["tasks"][0]["to_range"] == "^4.99.0"


def test_apply_release_digest_defaults_guide_empty_when_investigation_missing():
    plans = {"lodash": _bump_plan("lodash", "r1", "^4.0.0")}

    _apply_release_digest(plans, investigations={})

    assert plans["lodash"]["migration_guide"] == ""


def test_format_targets_includes_breaking_changes():
    targets = {"eslint": {"tier": "r2", "current_range": "7.0.0"}}
    investigations = {
        "eslint": {
            "dependents": [],
            "call_sites": [],
            "release": {
                "migration_needed": True,
                "to_version": "8.0.0",
                "migration_guide": "switch to flat config",
                "breaking_changes": ["flat config replaces .eslintrc"],
            },
        }
    }
    formatted = _format_targets(targets, investigations)
    assert "breaking_changes=['flat config replaces .eslintrc']" in formatted


def test_format_targets_breaking_changes_defaults_empty_list():
    targets = {"eslint": {"tier": "r1", "current_range": "7.0.0"}}
    investigations = {}
    formatted = _format_targets(targets, investigations)
    assert "breaking_changes=[]" in formatted
