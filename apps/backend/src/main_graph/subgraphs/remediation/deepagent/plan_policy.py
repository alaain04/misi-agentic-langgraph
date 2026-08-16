"""Classification of a target's MigrationPlan into a dispatch/settlement
decision -- replace (r3), no-op bump, or a real bump/codemod to execute.
Shared support logic (spec 2026-08-08-remediation-flatten-planning-
execution): remediate_targets_node uses it to decide what to dispatch,
group_and_verify_gate uses it to decide what a green replay actually fixed."""

from __future__ import annotations

from src.utils.semver import is_noop_range_change


def plan_kinds(plan: dict) -> set[str]:
    return {t.get("kind") for t in plan.get("tasks") or []}


def is_replace_target(target: dict, plan: dict) -> bool:
    """An r3 tier is binding on its own: classify decided no same-package
    upgrade fixes this dependency, so the target belongs on the replace path
    whether or not the planner actually wrote a `replace` task."""
    return (target or {}).get("tier") == "r3" or "replace" in plan_kinds(plan)


def replacement_proposal(plan: dict) -> dict:
    """The r3 plan's `replace` task, i.e. what to swap this dependency for.
    Empty when the plan has no replace task at all."""
    for task in plan.get("tasks") or []:
        if task.get("kind") == "replace":
            return task
    return {}


def replacement_skip_reason(proposal: dict) -> str:
    """Automating an r3 migration is still deferred (Spec B), but the
    proposal itself is the deliverable -- a named candidate and the reasoning
    behind it are what a human needs to act on. Say which of the two cases
    this is instead of reporting both as an undifferentiated deferral."""
    dep = proposal.get("replacement_dep")
    if not dep:
        return (
            "no same-package upgrade fixes this dependency and no replacement "
            "candidate was identified -- needs a manual choice of replacement"
        )
    target_range = proposal.get("replacement_range")
    named = f"{dep}@{target_range}" if target_range else dep
    return (
        f"replacement proposed: {named} -- review required, the migration is "
        f"not automated"
    )


def is_noop_bump_plan(plan: dict, current_range: str | None) -> bool:
    """True when a plan does nothing but re-declare the range package.json
    already has -- every task is a bump and no bump's to_range is provably
    an upgrade. Dispatching one costs a full install/build/test container
    cycle and can only land as an unexplained verification failure, so these
    settle as an honest skip instead of being executed."""
    tasks = plan.get("tasks") or []
    if not tasks or any(t.get("kind") != "bump" for t in tasks):
        return False
    return all(is_noop_range_change(current_range, t.get("to_range")) for t in tasks)
