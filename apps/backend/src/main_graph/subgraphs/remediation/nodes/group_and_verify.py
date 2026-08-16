from __future__ import annotations

import logging
import os
import shutil

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.deepagent.grouping import connected_groups
from src.main_graph.subgraphs.remediation.deepagent.plan_policy import is_noop_bump_plan
from src.main_graph.subgraphs.remediation.deepagent.replay import (
    replay_and_verify_group,
)
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.remediation import Remediation

logger = logging.getLogger(__name__)

_MAX_CORRECTION_ROUNDS = 2
_MAX_GROUPS = 20


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
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")
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
    verified_workdirs: dict[str, str] = {}
    keep_workdir = consent and bool(git_pr)

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

        replace_coupled = any(
            member["strategy"] == "replace" for member in members_dicts
        )
        # A group whose every member only ever planned a bump to the range
        # already declared has nothing to apply. Replaying it would verify a
        # pristine working copy, come back green, and mark the members
        # "fixed" -- claiming a remediation that changed no file.
        noop_only = all(
            is_noop_bump_plan(member.get("plan") or {}, member.get("from_range"))
            for member in members_dicts
        )
        if replace_coupled or noop_only:
            coupled_reason = (
                "coupled to a dependency migration (r3) target - deferred"
                if replace_coupled
                else "no upgrade available: the planned range matches the one "
                "already declared, so a bump would change nothing"
            )
            for member_dict in members_dicts:
                member_dict["status"] = "skipped"
                # The r3 member's own reason carries its replacement proposal
                # -- the whole point of settling it. Only its coupled
                # siblings get the generic group reason.
                if member_dict["strategy"] != "replace":
                    member_dict["skip_reason"] = coupled_reason
                member_dict["required_by"] = sorted(
                    required_by_map.get(member_dict["target_dep"], [])
                )
                settled[member_dict["target_dep"]] = member_dict
            continue

        members = [Remediation(**m) for m in members_dicts]
        verification, work_dir = await replay_and_verify_group(
            members,
            prep.repo_path,
            container,
            prep.docker_image,
            prep.package_manager,
            keep_workdir=keep_workdir,
        )
        group_ok = _is_green(verification)
        if work_dir:
            if group_ok:
                for dep in group:
                    verified_workdirs[dep] = work_dir
            else:
                shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
        for member_dict, member in zip(members_dicts, members, strict=True):
            member_dict["verification"] = verification.model_dump()
            member_dict["required_by"] = sorted(
                required_by_map.get(member.target_dep, [])
            )
            if group_ok:
                # A no-change member riding along in a group that a SIBLING's
                # real bump turned green did not itself fix anything.
                if is_noop_bump_plan(
                    member_dict.get("plan") or {}, member_dict.get("from_range")
                ):
                    member_dict["status"] = "skipped"
                    member_dict["skip_reason"] = (
                        "no upgrade available: the planned range matches the "
                        "one already declared, so a bump would change nothing"
                    )
                else:
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

    # `connected_groups` derives its groups from `target_deps` UNION every
    # name in `requires_edges` -- and `requires_edges` accumulates across
    # correction rounds (_merge_replace) while `targets` is narrowed to just
    # this round's retry deps. So a coupled group settled in an earlier round
    # gets re-verified here even though it was never re-dispatched, producing
    # either a brand-new kept copy (the old one then leaks) or no kept copy at
    # all (the prior round's entry survives the merge and would ship a stale
    # working copy). Delete any prior kept path this round superseded or
    # invalidated. Scope is `settled` -- exactly the deps this round rendered a
    # final per-round decision for; a dep still in missing-member retry limbo
    # hasn't been touched and must keep its copy.
    prior_workdirs: dict[str, str] = state.get("verified_workdirs") or {}
    stale_paths = {
        prior_workdirs[dep]
        for dep in settled
        if dep in prior_workdirs and prior_workdirs[dep] != verified_workdirs.get(dep)
    }
    for stale in stale_paths:
        shutil.rmtree(os.path.dirname(stale), ignore_errors=True)

    if retry_targets:
        return {
            "remediations": settled,
            "retry_targets": retry_targets,
            "correction_rounds": correction_rounds + 1,
            "verified_workdirs": verified_workdirs,
        }
    return {
        "remediations": settled,
        "retry_targets": [],
        "verified_workdirs": verified_workdirs,
    }


def route_after_group_verify(state: RemediationState) -> str:
    if state.get("retry_targets"):
        return "remediate_targets_node"
    return "pr_and_persist_node"
