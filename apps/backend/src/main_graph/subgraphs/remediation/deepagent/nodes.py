from __future__ import annotations

import logging
import os
import shutil

from deepagents import create_deep_agent
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.deepagent.grouping import connected_groups
from src.main_graph.subgraphs.remediation.deepagent.limits import (
    MAX_RETRIES,
    REMEDIATION_RATE_LIMITER,
)
from src.main_graph.subgraphs.remediation.deepagent.replay import (
    apply_group_changes,
    replay_and_verify_group,
)
from src.main_graph.subgraphs.remediation.deepagent.state import (
    RemediationDeepAgentState,
)
from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_codemod_subagent,
)
from src.main_graph.subgraphs.remediation.deepagent.tools import (
    make_commit_plan_tool,
)
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.models.remediation import (
    MigrationPlan,
    Remediation,
    RemediationOutcome,
    RemediationResult,
    RemediationTarget,
)
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_RECURSION_LIMIT = 50
_MAX_CORRECTION_ROUNDS = 2
_MAX_GROUPS = 20

_PLANNER_PROMPT = """\
You plan and delegate dependency remediation for a Node.js project. For each
open target you are given: the tier hint, the release digest (whether a
migration is needed and a guide), the dependents, and the call sites.

For EACH target you MUST:
1. Call commit_plan with a MigrationPlan: a `bump` task for a clean upgrade;
   `bump` + `codemod` task(s) when the release digest says migration_needed;
   a `replace` task only when the tier hint is r3. Put companion deps in
   `requires`.
2. Then dispatch codemod_adapter for every codemod task (give it the
   dependency name, the migration guide, and the affected files). Do NOT
   edit code yourself. Bump and replace tasks need no dispatch -- the root
   applies bumps deterministically and defers replace tasks.
Stop once every target has a committed plan and its codemod tasks are
dispatched."""


def _build_planning_agent(work_dir, container, docker_image, package_manager):
    return create_deep_agent(
        model=get_llm(
            Model.GPT_5_4_MINI,
            rate_limiter=REMEDIATION_RATE_LIMITER,
            max_retries=MAX_RETRIES,
        ),
        tools=[make_commit_plan_tool()],
        subagents=[
            build_codemod_subagent(work_dir, container, docker_image, package_manager),
        ],
        system_prompt=_PLANNER_PROMPT,
        state_schema=RemediationDeepAgentState,
    )


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
                target_dep=dep, addresses=[], current_range=direct.get(dep)
            ).model_dump()
        )
    return out


def _format_open_targets(
    targets: dict[str, dict], investigations: dict[str, dict]
) -> str:
    lines = ["Open targets:"]
    for dep, t in targets.items():
        inv = investigations.get(dep) or {}
        rel = inv.get("release") or {}
        lines.append(
            f"- {dep} (tier={t.get('tier')}, "
            f"addresses={t.get('addresses') or 'none'}, "
            f"migration_needed={rel.get('migration_needed')}, "
            f"call_sites={inv.get('call_sites') or []}, "
            f"guide={rel.get('migration_guide') or ''})"
        )
    return "\n".join(lines)


def _remediations_from_plans(
    targets: dict[str, dict],
    plans: dict[str, dict],
    outcomes: dict[str, dict],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dep, target_dict in targets.items():
        target = RemediationTarget(**target_dict)
        plan_dict = plans.get(dep)
        if plan_dict is None:
            out[dep] = Remediation(
                addresses=target.addresses,
                target_dep=dep,
                from_range=target.current_range,
                status="failed",
                skip_reason="planner produced no MigrationPlan",
            ).model_dump()
            continue
        plan = MigrationPlan(**plan_dict)
        kinds = {t.kind for t in plan.tasks}
        if "replace" in kinds:
            rem = Remediation(
                addresses=target.addresses,
                target_dep=dep,
                strategy="replace",
                from_range=target.current_range,
                status="skipped",
                skip_reason="dependency replacement deferred (Spec B)",
                plan=plan,
            )
        elif dep in outcomes:
            outcome = RemediationOutcome(**outcomes[dep])
            rem = Remediation(
                addresses=target.addresses,
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
        elif "codemod" in kinds:
            rem = Remediation(
                addresses=target.addresses,
                target_dep=dep,
                from_range=target.current_range,
                status="failed",
                skip_reason="codemod produced no outcome",
                plan=plan,
            )
        else:
            bump = next((t for t in plan.tasks if t.kind == "bump"), None)
            rem = Remediation(
                addresses=target.addresses,
                target_dep=dep,
                strategy="bump",
                from_range=target.current_range,
                to_range=bump.to_range if bump else None,
                status="skipped",  # provisional; gate sets real status
                plan=plan,
            )
        out[dep] = rem.model_dump()
    return out


async def root_deepagent_node(state: RemediationState, config: RunnableConfig) -> dict:
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
    open_list = _format_open_targets(targets, investigations)

    work_dir = copy_repo(prep.repo_path)
    try:
        agent = _build_planning_agent(
            work_dir, container, prep.docker_image, prep.detected_package_manager
        )
        initial_state = {
            "messages": [{"role": "user", "content": open_list}],
            "job_id": state["job_id"],
            "prep_result_id": state["prep_result_id"],
            "targets": targets,
            "remediations": {},
            "requires_edges": {},
            "migration_plans": {},
            "outcomes": {},
        }
        run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
        try:
            result = await agent.ainvoke(initial_state, run_config)
        except GraphRecursionError:
            # Spec D10: every bound (recursion limit, correction-round cap,
            # group cap) must fail honestly into skipped/failed with a
            # reason instead of crashing the job. Nothing from an aborted
            # run is trustworthy, so discard remediations/requires_edges/
            # migration_plans entirely -- group_and_verify_gate then sees
            # every target in this round as never-dispatched and routes it
            # through the same retry mechanism used for a partial group,
            # eventually failing honestly at the correction-round cap
            # rather than propagating the exception.
            logger.warning(
                "root_deepagent_node: hit recursion_limit=%d before "
                "finishing; discarding this round's in-progress work",
                _RECURSION_LIMIT,
            )
            return {
                "targets": targets,
                "remediations": {},
                "requires_edges": {},
                "migration_plans": {},
            }
    finally:
        shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)

    plans = result.get("migration_plans") or {}
    outcomes = result.get("outcomes") or {}
    remediations = _remediations_from_plans(targets, plans, outcomes)
    requires_edges = {
        dep: plan["requires"] for dep, plan in plans.items() if plan.get("requires")
    }
    return {
        "targets": targets,
        "remediations": remediations,
        "requires_edges": requires_edges,
        "migration_plans": plans,
    }


def _is_green(v) -> bool:
    return (
        v.installed
        and v.built is not False
        and v.tested is not False
        and v.finding_resolved is not False
    )


async def group_and_verify_gate(
    state: RemediationState, config: RunnableConfig
) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    remediations: dict[str, dict] = dict(state.get("remediations") or {})
    requires_edges: dict[str, list] = state.get("requires_edges") or {}
    target_deps = list(state.get("targets") or {})
    correction_rounds = state.get("correction_rounds", 0)

    required_by_map: dict[str, list[str]] = {}
    for target, requires in requires_edges.items():
        for required in requires:
            required_by_map.setdefault(required, []).append(target)

    groups = connected_groups(target_deps, requires_edges)

    settled: dict[str, dict] = {}
    retry_targets: list[str] = []

    for group in groups[:_MAX_GROUPS]:
        members_dicts = [remediations[dep] for dep in group if dep in remediations]
        if len(members_dicts) != len(group):
            missing = [dep for dep in group if dep not in remediations]
            if correction_rounds < _MAX_CORRECTION_ROUNDS:
                # A member named only via `requires` (never in the original
                # select_remediation_targets output) has no Remediation
                # record yet -- it was never dispatched, not dispatched-
                # and-failed. Route it through the same retry mechanism
                # used for failed verification instead of immediately
                # failing the whole group: root_deepagent_node's retry-mode
                # branch synthesizes a target entry for any retry_targets
                # name not already in state["targets"] and explicitly
                # instructs the root to dispatch it by name. Leave this
                # group's already-dispatched members untouched in
                # `remediations` (the outer state's _merge_replace reducer
                # preserves them across rounds) and don't settle anything
                # from this group yet -- its fate is decided once all
                # members exist.
                retry_targets.extend(missing)
                continue
            for member_dict in members_dicts:
                member_dict["status"] = "failed"
                member_dict["skip_reason"] = member_dict.get("skip_reason") or (
                    "a sibling dependency in this group was never dispatched"
                )
                member_dict["required_by"] = sorted(
                    required_by_map.get(member_dict["target_dep"], [])
                )
                settled[member_dict["target_dep"]] = member_dict
            continue

        if any(member["strategy"] == "replace" for member in members_dicts):
            for member_dict in members_dicts:
                member_dict["status"] = "skipped"
                member_dict["skip_reason"] = (
                    "coupled to a dependency migration (r3) target - deferred"
                )
                member_dict["required_by"] = sorted(
                    required_by_map.get(member_dict["target_dep"], [])
                )
                settled[member_dict["target_dep"]] = member_dict
            continue

        members = [Remediation(**m) for m in members_dicts]
        verification = await replay_and_verify_group(
            members,
            prep.repo_path,
            container,
            prep.docker_image,
            prep.detected_package_manager,
        )
        group_ok = _is_green(verification)
        for member_dict, member in zip(members_dicts, members, strict=True):
            member_dict["verification"] = verification.model_dump()
            member_dict["required_by"] = sorted(
                required_by_map.get(member.target_dep, [])
            )
            if group_ok:
                member_dict["status"] = "fixed"
            elif correction_rounds < _MAX_CORRECTION_ROUNDS:
                retry_targets.append(member.target_dep)
            else:
                member_dict["status"] = "failed"
                member_dict["skip_reason"] = member_dict.get("skip_reason") or (
                    "verification failed after max correction rounds"
                )
            settled[member.target_dep] = member_dict

    for group in groups[_MAX_GROUPS:]:
        for dep in group:
            if dep in remediations:
                remediations[dep]["status"] = "skipped"
                remediations[dep]["skip_reason"] = "target/group cap exceeded"
                remediations[dep]["required_by"] = sorted(required_by_map.get(dep, []))
                settled[dep] = remediations[dep]

    if retry_targets:
        return {
            "remediations": settled,
            "retry_targets": retry_targets,
            "correction_rounds": correction_rounds + 1,
        }
    return {"remediations": settled, "retry_targets": []}


def route_after_group_verify(state: RemediationState) -> str:
    return (
        "root_deepagent_node" if state.get("retry_targets") else "pr_and_persist_node"
    )


def _pr_title_and_body(group_remediations: list[Remediation]) -> tuple[str, str]:
    strategies = {r.strategy for r in group_remediations}
    if "replace" in strategies:
        label = "replace - review required"
    elif "bump_with_codemod" in strategies:
        label = "codemod - review required"
    else:
        label = "bump"
    deps = ", ".join(sorted(r.target_dep for r in group_remediations))
    title = f"Remediate {deps} ({label})"
    lines = [f"Automated dependency remediation - {label} (verified in sandbox):", ""]
    for r in group_remediations:
        if r.strategy == "replace":
            change = f"replace with {r.replacement_dep} {r.replacement_range}"
        else:
            change = f"{r.from_range} -> {r.to_range}"
        addresses = f" (fixes: {', '.join(r.addresses)})" if r.addresses else ""
        reason = f" (required by {', '.join(r.required_by)})" if r.required_by else ""
        lines.append(f"- {r.target_dep}: {change}{addresses}{reason}")
        if r.migration_plan:
            lines.append(f"  migration notes: {r.migration_plan}")
    return title, "\n".join(lines)


async def pr_and_persist_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")
    prep = await dao.get_prep(state["prep_result_id"])

    remediations = {
        dep: Remediation(**r) for dep, r in (state.get("remediations") or {}).items()
    }
    requires_edges = state.get("requires_edges") or {}
    groups = connected_groups(list(remediations), requires_edges)

    for group in groups:
        members = [remediations[dep] for dep in group if dep in remediations]
        if not members or not all(m.status == "fixed" for m in members):
            continue
        if consent and git_pr:
            work_dir = copy_repo(prep.repo_path)
            try:
                if not await apply_group_changes(work_dir, members):
                    logger.warning(
                        "pr_and_persist_node: replay failed for group %s, skipping PR",
                        group,
                    )
                    continue
                branch = f"remediation/{state['job_id'][:8]}-{group[0]}"
                title, body = _pr_title_and_body(members)
                try:
                    pr_url = await git_pr.open_pr(work_dir, branch, title, body)
                    for member in members:
                        member.branch = branch
                        member.pr_url = pr_url
                except Exception as exc:
                    logger.warning(
                        "pr_and_persist_node: PR creation failed for group %s: %s",
                        group,
                        exc,
                    )
            finally:
                shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)

    result = RemediationResult(
        job_id=state["job_id"],
        remediations=list(remediations.values()),
        consent=consent,
    )
    rid = await dao.save_remediation(result)
    return {"remediation_result_id": rid}
