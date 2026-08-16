from __future__ import annotations

import logging
import os
import shutil

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.remediation import (
    FindingSummary,
    Remediation,
    RemediationResult,
    VerificationResult,
)

logger = logging.getLogger(__name__)

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
        return collapsed.replace("|", "\\|")
    return collapsed[: limit - 1].rstrip().replace("|", "\\|") + "…"


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
            rows.append(f"| {finding} | {severity} | {description} | {r.target_dep} |")
    if not rows:
        return "None."
    header = (
        "| Finding | Severity | Description | Resolved by |\n| --- | --- | --- | --- |"
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
    consent = bool(svc.get("remediate"))
    git_pr = svc.get("git_pr")

    remediations = {
        dep: Remediation(**r) for dep, r in (state.get("remediations") or {}).items()
    }
    verified_workdirs: dict[str, str] = state.get("verified_workdirs") or {}

    by_workdir: dict[str, list[str]] = {}
    for dep, work_dir in verified_workdirs.items():
        by_workdir.setdefault(work_dir, []).append(dep)

    for work_dir, deps in by_workdir.items():
        members = [remediations[dep] for dep in deps if dep in remediations]
        # `verified_workdirs` can carry a dangling entry from an earlier
        # correction round (its reducer cannot delete keys, only overwrite
        # them), so re-check the gate's own verdict before shipping: every
        # member must actually be `fixed`. This respects the gate's decision
        # rather than making a new one. `consent` is checked here too so the
        # node defends itself instead of relying on the gate never populating
        # the channel without it.
        if (
            not members
            or git_pr is None
            or not consent
            or not all(m.status == "fixed" for m in members)
        ):
            shutil.rmtree(os.path.dirname(work_dir), ignore_errors=True)
            continue
        try:
            branch = f"remediation/{state['job_id'][:8]}-{sorted(deps)[0]}"
            title, body = _pr_title_and_body(members, members[0].verification)
            pr_url = await git_pr.open_pr(work_dir, branch, title, body)
            for member in members:
                member.branch = branch
                member.pr_url = pr_url
        except Exception as exc:
            logger.warning(
                "pr_and_persist_node: PR creation failed for group %s: %s",
                deps,
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
