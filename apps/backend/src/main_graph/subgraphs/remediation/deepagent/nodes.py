from __future__ import annotations

import logging

from deepagents import create_deep_agent
from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.deepagent.grouping import connected_groups
from src.main_graph.subgraphs.remediation.deepagent.replay import (
    apply_group_changes,
    replay_and_verify_group,
)
from src.main_graph.subgraphs.remediation.deepagent.state import (
    RemediationDeepAgentState,
)
from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_target_subagent,
)
from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.workspace import copy_repo
from src.main_graph.tools.npm_cli import npm_audit, npm_outdated
from src.models.remediation import Remediation, RemediationResult
from src.utils.config import settings
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_RECURSION_LIMIT = 50
_MAX_CORRECTION_ROUNDS = 2
_MAX_GROUPS = 20


def _build_root_deep_agent():
    return create_deep_agent(
        model=get_llm(Model.GPT_5_4_MINI),
        tools=[],
        subagents=[build_target_subagent()],
        system_prompt=(
            "You coordinate dependency remediation for a Node.js project. "
            "For each open target listed in the first message, call the "
            "remediate_target tool, describing the dependency by name. If "
            "a call's result says another dependency is also required, "
            "dispatch that dependency too, unless it is already open or "
            "already remediated. Stop once every target you know about "
            "has been dispatched."
        ),
        state_schema=RemediationDeepAgentState,
    )


_root_deep_agent = _build_root_deep_agent()


async def root_deepagent_node(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    prep = await dao.get_prep(state["prep_result_id"])

    retry_targets = state.get("retry_targets")
    if retry_targets:
        targets = {
            dep: t
            for dep, t in (state.get("targets") or {}).items()
            if dep in retry_targets
        }
        evidence = state.get("evidence") or {}
    else:
        analysis = await dao.get_analysis(state["analysis_result_id"])
        initial = select_remediation_targets(
            analysis.findings, prep.dependency_graph, settings.risk_min_severity
        )
        targets = {t.target_dep: t.model_dump() for t in initial}
        if not targets:
            return {"targets": {}, "remediations": {}, "requires_edges": {}}
        audit = await npm_audit(
            prep.repo_path, container, prep.docker_image, prep.detected_package_manager
        )
        outdated = await npm_outdated(prep.repo_path, container, prep.docker_image)
        evidence = {"audit": audit, "outdated": outdated}

    if not targets:
        return {"targets": {}, "remediations": {}, "requires_edges": {}}

    open_list = "\n".join(
        f"- {dep} (addresses: {', '.join(t['addresses']) or 'none'})"
        for dep, t in targets.items()
    )
    initial_state = {
        "messages": [{"role": "user", "content": f"Open targets:\n{open_list}"}],
        "job_id": state["job_id"],
        "prep_result_id": state["prep_result_id"],
        "evidence": evidence,
        "targets": targets,
        "remediations": {},
        "requires_edges": {},
    }
    run_config = {**config, "recursion_limit": _RECURSION_LIMIT}
    result = await _root_deep_agent.ainvoke(initial_state, run_config)

    return {
        "targets": targets,
        "evidence": evidence,
        "remediations": result.get("remediations") or {},
        "requires_edges": result.get("requires_edges") or {},
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

    result = RemediationResult(
        job_id=state["job_id"],
        remediations=list(remediations.values()),
        consent=consent,
    )
    rid = await dao.save_remediation(result)
    return {"remediation_result_id": rid}
