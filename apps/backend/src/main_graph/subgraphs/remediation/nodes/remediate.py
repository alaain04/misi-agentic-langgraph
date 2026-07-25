from __future__ import annotations

import logging
import os
import shutil

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.orchestrator import run_remediation
from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import (
    apply_bump,
    copy_repo,
    working_copy_diff,
)
from src.main_graph.tools.npm_cli import npm_audit, npm_outdated
from src.models.remediation import RemediationResult
from src.utils.config import settings

logger = logging.getLogger(__name__)


def _pr_body(remediations) -> tuple[str, str]:
    fixed = [r for r in remediations if r.status == "fixed"]
    title = f"Remediate {len(fixed)} dependency finding(s)"
    lines = ["Automated dependency remediation (verified in sandbox):", ""]
    for r in fixed:
        note = []
        if r.verification.tested is None:
            note.append("no tests in repo")
        if r.verification.finding_resolved is None:
            note.append("resolution not deterministically checkable")
        suffix = f" ({'; '.join(note)})" if note else ""
        lines.append(
            f"- {r.target_dep} {r.from_range} -> {r.to_range} "
            f"(fixes: {', '.join(r.addresses)}){suffix}"
        )
    return title, "\n".join(lines)


async def remediate(state: RemediationState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    container = svc["container"]
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")

    analysis = await dao.get_analysis(state["analysis_result_id"])
    prep = await dao.get_prep(state["prep_result_id"])

    targets = select_remediation_targets(
        analysis.findings, prep.dependency_graph, settings.risk_min_severity
    )
    logger.info("remediate: %d target(s) after selection", len(targets))

    if not targets:
        rid = await dao.save_remediation(
            RemediationResult(job_id=state["job_id"], consent=consent)
        )
        return {"remediation_result_id": rid}

    work_dir = copy_repo(prep.repo_path)
    try:
        audit = await npm_audit(
            prep.repo_path, container, prep.docker_image, prep.detected_package_manager
        )
        outdated = await npm_outdated(prep.repo_path, container, prep.docker_image)

        async def verify(deps):
            return await verify_working_copy(
                work_dir, container, prep.docker_image,
                prep.detected_package_manager, deps,
            )

        async def diff():
            return await working_copy_diff(work_dir)

        remediations = await run_remediation(
            targets, work_dir, {"audit": audit, "outdated": outdated},
            apply_bump, verify, diff,
        )

        result = RemediationResult(
            job_id=state["job_id"], remediations=remediations, consent=consent
        )

        if consent and git_pr and any(r.status == "fixed" for r in remediations):
            branch = f"remediation/{state['job_id'][:8]}"
            title, body = _pr_body(remediations)
            try:
                result.pr_url = await git_pr.open_pr(work_dir, branch, title, body)
                result.branch = branch
            except Exception as exc:
                logger.warning("remediate: PR creation failed: %s", exc)

        rid = await dao.save_remediation(result)
        return {"remediation_result_id": rid}
    finally:
        shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
