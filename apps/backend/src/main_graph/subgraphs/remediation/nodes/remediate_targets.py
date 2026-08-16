from __future__ import annotations

import asyncio
import logging
import os
import shutil

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.deepagent.grouping import connected_groups
from src.main_graph.subgraphs.remediation.deepagent.limits import TARGET_SEMAPHORE
from src.main_graph.subgraphs.remediation.deepagent.plan_policy import (
    is_noop_bump_plan,
    is_replace_target,
    plan_kinds,
    replacement_proposal,
    replacement_skip_reason,
)
from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_execution_agent,
)
from src.main_graph.subgraphs.remediation.nodes.plan import build_plans_for_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.models.remediation import (
    MigrationPlan,
    Remediation,
    RemediationOutcome,
    RemediationTarget,
)

logger = logging.getLogger(__name__)

_RECURSION_LIMIT = 50


def _resolve_working_targets(state: RemediationState, prep) -> dict[str, dict]:
    retry_targets = state.get("retry_targets")
    known = state.get("targets") or {}
    if not retry_targets:
        return known
    direct = prep.dependency_graph.get("direct") or {}
    out: dict[str, dict] = {}
    for dep in retry_targets:
        out[dep] = (
            known.get(dep)
            or RemediationTarget(
                target_dep=dep,
                addresses=[],
                finding_summaries=[],
                current_range=direct.get(dep),
            ).model_dump()
        )
    return out


def _format_group_message(
    group: list[str],
    plans: dict[str, dict],
    investigations: dict[str, dict],
    failures: dict[str, dict],
) -> str:
    lines = ["Targets in this working copy:"]
    for dep in group:
        plan = plans.get(dep) or {}
        inv = investigations.get(dep) or {}
        kinds = sorted(plan_kinds(plan)) or ["bump"]
        lines.append(
            f"- {dep}: kind={'+'.join(kinds)}, "
            f"guide={plan.get('migration_guide') or 'none'}, "
            f"call_sites={inv.get('call_sites') or []}"
        )
        failure = failures.get(dep)
        if failure and failure.get("logs_snippet"):
            lines.append(
                "  PREVIOUS ATTEMPT FAILED verification -- diagnose and fix "
                f"this instead of repeating the same change: "
                f"{failure['logs_snippet']}"
            )
    return "\n".join(lines)


async def _run_group(
    group: list[str],
    targets: dict[str, dict],
    plans: dict[str, dict],
    investigations: dict[str, dict],
    failures: dict[str, dict],
    prep,
    container,
    config: RunnableConfig,
) -> dict[str, dict] | None:
    """Invoke ONE flat execution agent directly for this group (never via
    deepagents' task() tool -- see subagent_wrapper.py). Returns this
    group's {target_dep: RemediationOutcome dict} outcomes, or None if the
    agent hit its recursion limit (the caller then omits this group's
    targets from `remediations` entirely, which routes them through
    group_and_verify_gate's existing missing-member retry path instead of
    marking them individually failed)."""
    work_dir = copy_repo(prep.repo_path)
    try:
        resolved_repos = {
            dep: (targets.get(dep) or {}).get("resolved_repo") for dep in group
        }
        agent = build_execution_agent(
            work_dir,
            container,
            prep.docker_image,
            prep.package_manager,
            resolved_repos,
        )
        message = _format_group_message(group, plans, investigations, failures)
        initial_state = {
            "messages": [{"role": "user", "content": message}],
            "outcomes": {},
        }
        run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
        async with TARGET_SEMAPHORE:
            try:
                result = await agent.ainvoke(initial_state, run_config)
            except GraphRecursionError:
                logger.warning(
                    "_run_group: hit recursion_limit=%d for group %s before "
                    "finishing; discarding this round's in-progress work",
                    _RECURSION_LIMIT,
                    group,
                )
                return None
        return result.get("outcomes") or {}
    finally:
        shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)


def _assemble_remediations(
    targets: dict[str, dict],
    plans: dict[str, dict],
    outcomes: dict[str, dict],
    omit: set[str],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dep, target_dict in targets.items():
        if dep in omit:
            continue
        target = RemediationTarget(**target_dict)
        plan_dict = plans.get(dep)
        if plan_dict is None:
            out[dep] = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                from_range=target.current_range,
                status="failed",
                skip_reason="planner produced no MigrationPlan",
            ).model_dump()
            continue
        plan = MigrationPlan(**plan_dict)
        if is_replace_target(target_dict, plan_dict):
            proposal = replacement_proposal(plan_dict)
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                strategy="replace",
                from_range=target.current_range,
                replacement_dep=proposal.get("replacement_dep"),
                replacement_range=proposal.get("replacement_range"),
                migration_plan=proposal.get("rationale") or "",
                status="skipped",
                skip_reason=replacement_skip_reason(proposal),
                plan=plan,
            )
        elif is_noop_bump_plan(plan_dict, target.current_range):
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                from_range=target.current_range,
                status="skipped",
                skip_reason=(
                    "no upgrade available: the planned range matches the one "
                    "already declared, so a bump would change nothing"
                ),
                plan=plan,
            )
        elif dep in outcomes:
            outcome = RemediationOutcome(**outcomes[dep])
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                strategy=outcome.strategy,
                from_range=target.current_range,
                to_range=outcome.to_range,
                replacement_dep=outcome.replacement_dep,
                replacement_range=outcome.replacement_range,
                migration_plan=outcome.migration_plan,
                patch=outcome.code_diff,
                status="skipped",  # provisional; gate sets real status
                skip_reason=outcome.skip_reason,
                plan=plan,
            )
        else:
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                from_range=target.current_range,
                status="failed",
                skip_reason="execution agent produced no outcome",
                plan=plan,
            )
        out[dep] = rem.model_dump()
    return out


async def remediate_targets_node(
    state: RemediationState, config: RunnableConfig
) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    targets = _resolve_working_targets(state, prep)
    if not targets:
        return {
            "targets": {},
            "remediations": {},
            "requires_edges": {},
            "migration_plans": {},
        }

    investigations = state.get("investigations") or {}
    plans = dict(state.get("migration_plans") or {})

    # A dep named only via some other target's `requires` (never in the
    # original selection) has no plan from build_migration_plan_node's
    # batch call -- plan it now, scoped to just this dep, not a re-run of
    # the whole batch.
    unplanned = {dep: t for dep, t in targets.items() if dep not in plans}
    if unplanned:
        plans.update(await build_plans_for_targets(unplanned, investigations))

    # classify's r3 verdict is binding, not advisory. Routing used to key
    # only off the plan's task kinds, so an r3 target whose plan carried a
    # bump task (the planner ignoring its own tier_hint) was dispatched to
    # the execution agent and "bumped" an abandoned package to the version
    # already installed. Tier is checked here too so a planner slip cannot
    # send a target with no same-package fix down the bump path.
    replace_deps = {
        dep for dep in targets if is_replace_target(targets[dep], plans.get(dep, {}))
    }
    # A bump to the range already declared is a no-op; executing it burns a
    # container verify cycle only to fail with a reason that reads like a
    # real defect. _assemble_remediations settles these as skipped.
    noop_deps = {
        dep
        for dep in targets
        if dep not in replace_deps
        and is_noop_bump_plan(
            plans.get(dep) or {}, (targets[dep] or {}).get("current_range")
        )
    }
    # A dep with no plan at all (the batch/single-target planning call
    # failed or omitted it) has nothing to give the execution agent -- fail
    # it honestly via _assemble_remediations instead of dispatching an
    # agent with no guidance.
    exec_deps = [
        dep
        for dep in targets
        if dep in plans and dep not in replace_deps and dep not in noop_deps
    ]

    requires_edges = {
        dep: plans[dep]["requires"]
        for dep in targets
        if plans.get(dep, {}).get("requires")
    }
    # Grouping can pull in names that are only ever a `requires` value (a
    # companion never independently selected, or a replace-tier dep another
    # target happens to require) -- neither has an exec plan to dispatch on
    # this round. Filter each group down to real exec targets before
    # dispatch; group_and_verify_gate's own (unfiltered) grouping still
    # catches a companion-only name via its missing-member retry branch.
    groups = [
        [dep for dep in group if dep in exec_deps]
        for group in connected_groups(exec_deps, requires_edges)
    ]
    groups = [g for g in groups if g]

    prior_remediations = state.get("remediations") or {}
    retrying = set(state.get("retry_targets") or [])

    def _failures_for(group: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for dep in group:
            if dep not in retrying:
                continue
            verification = (prior_remediations.get(dep) or {}).get("verification") or {}
            if verification.get("logs_snippet"):
                out[dep] = {"logs_snippet": verification["logs_snippet"]}
        return out

    async def _bounded(group: list[str]) -> tuple[list[str], dict[str, dict] | None]:
        outcomes = await _run_group(
            group,
            targets,
            plans,
            investigations,
            _failures_for(group),
            prep,
            container,
            config,
        )
        return group, outcomes

    # asyncio.gather does not cancel still-running siblings when one task
    # raises -- an unrecoverable error in one group (e.g. a RateLimitError
    # that exhausts its retry budget) would otherwise leave the other
    # groups' agents running as untracked orphans (still calling the LLM,
    # still holding a work_dir/container) after this node has already
    # failed. TaskGroup cancels them. except* unwraps the resulting
    # ExceptionGroup back to the original exception so job_runner's
    # `error=str(exc)` still stores the real message instead of TaskGroup's
    # generic wrapper text.
    tasks: list[asyncio.Task] = []
    try:
        async with asyncio.TaskGroup() as tg:
            for group in groups:
                tasks.append(tg.create_task(_bounded(group)))
    except* Exception as eg:
        raise eg.exceptions[0] from eg

    group_results = [t.result() for t in tasks]

    outcomes: dict[str, dict] = {}
    recursion_hit: set[str] = set()
    for group, group_outcomes in group_results:
        if group_outcomes is None:
            recursion_hit.update(group)
            continue
        outcomes.update(group_outcomes)

    remediations = _assemble_remediations(targets, plans, outcomes, recursion_hit)
    return {
        "targets": targets,
        "remediations": remediations,
        "requires_edges": requires_edges,
        "migration_plans": plans,
    }
