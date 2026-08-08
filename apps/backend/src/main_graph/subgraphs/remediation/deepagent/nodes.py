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
from src.main_graph.subgraphs.remediation.deepagent.replay import (
    apply_group_changes,
    replay_and_verify_group,
)
from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_execution_agent,
)
from src.main_graph.subgraphs.remediation.plan import build_plans_for_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.models.remediation import (
    FindingSummary,
    MigrationPlan,
    Remediation,
    RemediationOutcome,
    RemediationResult,
    RemediationTarget,
    VerificationResult,
)

logger = logging.getLogger(__name__)

_RECURSION_LIMIT = 50
_MAX_CORRECTION_ROUNDS = 2
_MAX_GROUPS = 20


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


def _plan_kinds(plan: dict) -> set[str]:
    return {t.get("kind") for t in plan.get("tasks") or []}


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
        kinds = sorted(_plan_kinds(plan)) or ["bump"]
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
        agent = build_execution_agent(
            work_dir, container, prep.docker_image, prep.detected_package_manager
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
        if "replace" in _plan_kinds(plan_dict):
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
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

    replace_deps = {
        dep for dep in targets if "replace" in _plan_kinds(plans.get(dep, {}))
    }
    # A dep with no plan at all (the batch/single-target planning call
    # failed or omitted it) has nothing to give the execution agent -- fail
    # it honestly via _assemble_remediations instead of dispatching an
    # agent with no guidance.
    exec_deps = [dep for dep in targets if dep in plans and dep not in replace_deps]

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
            group, plans, investigations, _failures_for(group), prep, container, config
        )
        return group, outcomes

    group_results = await asyncio.gather(*[_bounded(g) for g in groups])

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
                # failing the whole group: remediate_targets_node's retry-
                # mode branch synthesizes a target entry for any
                # retry_targets name not already in state["targets"] and
                # explicitly instructs the root to dispatch it by name.
                # Leave this group's already-dispatched members untouched
                # in `remediations` (the outer state's _merge_replace
                # reducer preserves them across rounds) and don't settle
                # anything from this group yet -- its fate is decided once
                # all members exist.
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
    if state.get("retry_targets"):
        return "remediate_targets_node"
    return "pr_and_persist_node"


# Reference layout for a generated remediation PR body. Keep new sections
# additive to this shape rather than inventing a one-off format per caller.
_PR_BODY_TEMPLATE = """\
## Summary

{summary}

## Changes

{changes_table}

## Findings addressed

{findings_table}

## Verification

{verification}
{migration_notes}"""


def _pr_strategy_label(group_remediations: list[Remediation]) -> str:
    strategies = {r.strategy for r in group_remediations}
    if "replace" in strategies:
        return "replace"
    if "bump_with_codemod" in strategies:
        return "codemod"
    return "bump"


def _pr_summary(group_remediations: list[Remediation], label: str) -> str:
    dep_count = len(group_remediations)
    finding_count = len(
        {f for r in group_remediations for f in (r.addresses or [r.target_dep])}
    )
    dep_word = "dependency" if dep_count == 1 else "dependencies"
    finding_word = "finding" if finding_count == 1 else "findings"
    summary = (
        f"- Fixes {dep_count} {dep_word}, resolving {finding_count} {finding_word}."
    )
    lines = [summary]
    if label != "bump":
        lines.append(f"- Strategy: {label} -- please review before merging.")
    return "\n".join(lines)


def _pr_changes_table(group_remediations: list[Remediation]) -> str:
    header = (
        "| Dependency | Strategy | Change | Required by |\n| --- | --- | --- | --- |"
    )
    rows = []
    for r in group_remediations:
        if r.strategy == "replace":
            change = f"replaced with `{r.replacement_dep}@{r.replacement_range}`"
        else:
            change = f"`{r.from_range}` -> `{r.to_range}`"
        required_by = ", ".join(r.required_by) if r.required_by else "-"
        rows.append(f"| {r.target_dep} | {r.strategy} | {change} | {required_by} |")
    return "\n".join([header, *rows])


def _truncate(text: str, limit: int = 150) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _pr_findings_table(group_remediations: list[Remediation]) -> str:
    summaries: dict[str, FindingSummary] = {
        fs.dep_name: fs for r in group_remediations for fs in r.finding_summaries
    }
    rows = []
    for r in group_remediations:
        for finding in r.addresses or [r.target_dep]:
            summary = summaries.get(finding)
            severity = summary.severity if summary else "-"
            description = _truncate(summary.description) if summary else "-"
            rows.append(
                f"| {finding} | {severity} | {description} | {r.target_dep} |"
            )
    if not rows:
        return "None."
    header = (
        "| Finding | Severity | Description | Resolved by |\n"
        "| --- | --- | --- | --- |"
    )
    return "\n".join([header, *rows])


def _checkbox(passed: bool, label: str) -> str:
    return f"- [x] {label}" if passed else f"- [ ] {label} (failed)"


def _pr_verification_summary(verification: VerificationResult) -> str:
    lines = [_checkbox(verification.installed, "Install")]
    if verification.built is not None:
        lines.append(_checkbox(verification.built, "Build"))
    if verification.tested is not None:
        lines.append(_checkbox(verification.tested, "Tests"))
    if verification.finding_resolved is not None:
        resolved = (
            "finding no longer present"
            if verification.finding_resolved
            else "finding still present"
        )
        box = "x" if verification.finding_resolved else " "
        lines.append(f"- [{box}] Audit re-scan: {resolved}")
    return "\n".join(lines)


def _pr_title_and_body(
    group_remediations: list[Remediation], verification: VerificationResult
) -> tuple[str, str]:
    label = _pr_strategy_label(group_remediations)
    deps = ", ".join(sorted(r.target_dep for r in group_remediations))
    title_label = label if label == "bump" else f"{label} - review required"
    title = f"Remediate {deps} ({title_label})"

    migration_notes = "\n".join(
        f"- **{r.target_dep}**: {r.migration_plan}"
        for r in group_remediations
        if r.migration_plan
    )
    body = _PR_BODY_TEMPLATE.format(
        summary=_pr_summary(group_remediations, label),
        changes_table=_pr_changes_table(group_remediations),
        findings_table=_pr_findings_table(group_remediations),
        verification=_pr_verification_summary(verification),
        migration_notes=f"\n## Migration notes\n\n{migration_notes}\n"
        if migration_notes
        else "",
    )
    return title, body


async def pr_and_persist_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
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
                # group_and_verify_gate's verification ran install/build/test
                # against a throwaway replay copy that is deleted right after
                # -- it never touched this work_dir, so its lock file is
                # still the pre-bump one. Re-install here, on the copy that
                # actually gets committed, so the lock file matches the
                # bumped package.json before it ships.
                targeted = sorted(
                    {dep for m in members for dep in [m.target_dep, *m.addresses]}
                )
                verification = await verify_working_copy(
                    work_dir,
                    container,
                    prep.docker_image,
                    prep.detected_package_manager,
                    targeted,
                )
                if not _is_green(verification):
                    logger.warning(
                        "pr_and_persist_node: final install/verify failed for "
                        "group %s, skipping PR: %s",
                        group,
                        verification.logs_snippet,
                    )
                    continue
                branch = f"remediation/{state['job_id'][:8]}-{group[0]}"
                title, body = _pr_title_and_body(members, verification)
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
