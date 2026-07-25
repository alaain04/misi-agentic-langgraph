from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Literal, cast

from src.main_graph.subgraphs.remediation.workspace import working_copy_diff
from src.models.remediation import (
    Remediation,
    RemediationDecision,
    RemediationTarget,
    VerificationResult,
)
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You remediate Node.js dependency risks by proposing SAME-PACKAGE version bumps.
For each target (a direct dependency), propose a to_range that resolves the
findings it addresses, grounded in the audit fix path and outdated data below.

Rules:
- Only bumps. If the fix needs a breaking MAJOR upgrade or code changes, action
  "skip" with skip_reason "needs major (Tier 2)". If the recommendation is a
  DIFFERENT package, action "skip" with skip_reason "different package (Tier 3)".
- One action per turn. After a bump, you will see the whole-copy verification
  result; if it regressed, adjust a range or skip a target so the set verifies
  TOGETHER.
- action "finalize" when every target is fixed or skipped.

Evidence:
{evidence}

Targets and current status:
{status}

Applied bumps: {applied}
Last verification: {last_verification}
"""


def _infer_strategy(
    reason: str,
) -> Literal["bump", "bump_with_codemod", "replace"]:
    low = (reason or "").lower()
    if "package" in low or "replace" in low:
        return "replace"
    if "major" in low or "migration" in low or "codemod" in low:
        return "bump_with_codemod"
    return "bump"


def _is_green(v: VerificationResult) -> bool:
    return (
        v.installed
        and v.built is not False
        and v.tested is not False
        and v.finding_resolved is not False
    )


def _default_decider() -> Callable[[str], Awaitable[RemediationDecision]]:
    structured = _llm.with_structured_output(
        RemediationDecision, method="function_calling"
    )

    async def decide(prompt: str) -> RemediationDecision:
        return cast(
            RemediationDecision,
            await structured.ainvoke([{"role": "user", "content": prompt}]),
        )

    return decide


async def run_remediation(
    targets: list[RemediationTarget],
    work_dir: str,
    evidence: dict,
    apply_bump: Callable[[str, str, str], bool],
    verify: Callable[[list[str]], Awaitable[VerificationResult]],
    diff: Callable[[], Awaitable[str]] | None = None,
    decide: Callable[[str], Awaitable[RemediationDecision]] | None = None,
    max_iterations: int = 8,
) -> list[Remediation]:
    decide = decide or _default_decider()
    if diff is None:

        async def diff() -> str:  # noqa: E306
            return await working_copy_diff(work_dir)

    rem: dict[str, Remediation] = {
        t.target_dep: Remediation(
            addresses=t.addresses,
            target_dep=t.target_dep,
            from_range=t.current_range,
            status="skipped",
            skip_reason="not attempted",
        )
        for t in targets
    }
    by_dep = {t.target_dep: t for t in targets}
    applied: set[str] = set()
    last_v: VerificationResult | None = None
    verified_for: set[str] | None = None  # the `applied` snapshot last_v corresponds to

    def _targeted_for(deps: set[str]) -> list[str]:
        return sorted(deps | {a for d in deps for a in by_dep[d].addresses})

    for _ in range(max_iterations):
        prompt = _SYSTEM.format(
            evidence=json.dumps(evidence)[:4000],
            status=json.dumps({d: r.status for d, r in rem.items()}),
            applied=sorted(applied),
            last_verification=(last_v.model_dump() if last_v else "none"),
        )
        decision = await decide(prompt)

        if decision.action == "finalize":
            break

        dep = decision.target_dep or ""
        if dep not in rem:
            continue

        if decision.action == "skip":
            skip_reason = decision.skip_reason or "no fix"
            rem[dep].status = "skipped"
            rem[dep].skip_reason = skip_reason
            rem[dep].strategy = _infer_strategy(skip_reason)
            applied.discard(dep)
            continue

        # action == "bump"
        if not apply_bump(work_dir, dep, decision.to_range or ""):
            rem[dep].status = "failed"
            rem[dep].skip_reason = "dependency not declared in package.json"
            continue
        rem[dep].strategy = "bump"
        rem[dep].to_range = decision.to_range
        rem[dep].attempts += 1
        applied.add(dep)

        last_v = await verify(_targeted_for(applied))
        verified_for = set(applied)
        for d in applied:
            rem[d].verification = last_v

    # Status is decided ONCE here, from a verification of the FINAL applied
    # set — never incrementally inside the loop. A dep bumped early and
    # verified green must NOT ship as "fixed" if a later bump (of a
    # different target) regressed the joint working copy: the invariant is
    # that whatever lands in the PR verifies TOGETHER, not that each dep
    # individually verified at some point in its history. If nothing
    # changed `applied` since the last verify() call, reuse it instead of
    # re-running verification for free.
    if applied and verified_for != applied:
        last_v = await verify(_targeted_for(applied))
        verified_for = set(applied)
        for d in applied:
            rem[d].verification = last_v

    if applied:
        final_status: Literal["fixed", "failed"] = (
            "fixed" if (last_v is not None and _is_green(last_v)) else "failed"
        )
        for d in applied:
            rem[d].status = final_status

    patch = await diff() if any(r.status == "fixed" for r in rem.values()) else ""
    for r in rem.values():
        if r.status == "fixed":
            r.patch = patch
    return list(rem.values())
