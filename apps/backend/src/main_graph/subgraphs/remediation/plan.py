"""Planning phase (spec 2026-08-08-remediation-flatten-planning-execution,
D1): a deterministic node that emits every target's MigrationPlan via ONE
batched structured-output call, replacing the old plan_and_orchestrate
deepagent's planning half. Nothing here dispatches an agent -- planning is a
single decision made from classify_targets_node's already-gathered
evidence (tier, release digest, dependents, call sites)."""

from __future__ import annotations

import logging
import textwrap
from typing import cast

from langchain_core.runnables import RunnableConfig

from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.remediation import MigrationPlanBatch, MigrationTask
from src.utils.model_registry import AgentRole, get_role_llm

logger = logging.getLogger(__name__)

_llm = get_role_llm(AgentRole.REMEDIATION_PLAN)

_PLAN_SYSTEM_PROMPT = textwrap.dedent("""\
    You plan dependency remediation for a Node.js project. You are given a
    list of targets, each with a tier hint, a release digest (whether a
    migration is needed and a guide), its local dependents, and its call
    sites.

    For EACH target produce exactly one MigrationPlan with target_dep
    matching the target exactly. The tier hint decides the SHAPE of the
    plan:
    - tier_hint=r3: the package is deprecated, abandoned, or superseded, so
      a same-package upgrade is not a fix. Emit exactly one `replace` task
      and no `bump` task -- not even when the release digest says
      migration_needed is false (for an abandoned package that only means
      there are no newer releases to break anything).
      Name a concrete replacement_dep ONLY if you are confident it is a
      real, currently-maintained npm package that covers this dependency's
      use in this project. Put your reasoning in the task's rationale: what
      the replacement is, why it fits these call sites, and roughly what
      the migration involves. If you cannot name one you are confident
      exists, leave replacement_dep null and use the rationale to say what
      kind of package would be needed. An honest "no candidate" is
      required -- a plausible-sounding package name that does not exist is
      worse than nothing, because a human will act on it.
    - tier_hint=r1 or r2 with migration_needed=false: a single `bump` task.
    - tier_hint=r1 or r2 with migration_needed=true: a `bump` task plus
      `codemod` task(s). Give the codemod task the specific files from
      call_sites that need review.

    For a `bump` task's to_range, propose the newest version you are
    confident resolves STRICTLY HIGHER than current_range; leave it null
    and explain why in the rationale if you can't. The system overwrites
    this with the known target release version whenever the digest
    provides one, so don't worry about matching it exactly.

    Leave migration_guide empty -- the system copies it in from the release
    digest afterward.

    If a target's fix requires bumping a companion dependency together with
    it (a peer dependency, a version lockstep), list that companion's name
    in `requires` -- even if the companion is not itself one of the targets
    given; it will be resolved separately. Do not invent a requirement
    without a concrete reason from the evidence given.
    """).strip()


def _format_targets(targets: dict[str, dict], investigations: dict[str, dict]) -> str:
    lines = ["Targets:"]
    for dep, t in targets.items():
        inv = investigations.get(dep) or {}
        rel = inv.get("release") or {}
        lines.append(
            f"- target_dep={dep} tier_hint={t.get('tier') or 'r1'} "
            f"current_range={t.get('current_range') or 'unknown'} "
            f"dependents={inv.get('dependents') or []} "
            f"call_sites={inv.get('call_sites') or []} "
            f"migration_needed={rel.get('migration_needed')} "
            f"to_version={rel.get('to_version') or 'unknown'} "
            f"migration_guide={rel.get('migration_guide') or 'none'}"
        )
    return "\n".join(lines)


def _apply_release_digest(
    plans: dict[str, dict], investigations: dict[str, dict]
) -> None:
    """Fill migration_guide and a known to_range straight from the release
    digest classify_target already gathered, rather than trusting the model
    to reproduce them: exact text reproduction and copying a known version
    number are not judgment calls, and an LLM asked to reproduce long text
    verbatim is unreliable at it."""
    for dep, plan in plans.items():
        release = (investigations.get(dep) or {}).get("release") or {}
        plan["migration_guide"] = release.get("migration_guide") or ""
        to_version = release.get("to_version")
        if not to_version:
            continue
        for task in plan.get("tasks") or []:
            if task.get("kind") == "bump":
                task["to_range"] = to_version


def _enforce_tier(plans: dict[str, dict], targets: dict[str, dict]) -> None:
    for dep, plan in plans.items():
        target = targets.get(dep) or {}
        tier = target.get("tier")
        tasks = plan.get("tasks") or []

        if tier == "r3":
            filtered = []
            dropped_bump = 0
            dropped_extra_replace = 0
            replace_count = 0
            for task in tasks:
                kind = task.get("kind")
                if kind == "bump":
                    dropped_bump += 1
                    continue
                if kind == "replace":
                    replace_count += 1
                    if replace_count > 1:
                        dropped_extra_replace += 1
                        continue
                filtered.append(task)
            if dropped_bump:
                logger.warning(
                    "_enforce_tier: dropped %d bump task(s) from r3 plan for %s "
                    "(a deprecated/superseded package has no same-package fix)",
                    dropped_bump,
                    dep,
                )
            if dropped_extra_replace:
                logger.warning(
                    "_enforce_tier: r3 plan for %s named %d replace tasks; "
                    "kept the first and dropped the rest",
                    dep,
                    dropped_extra_replace + 1,
                )
            if replace_count == 0:
                logger.warning(
                    "_enforce_tier: r3 plan for %s named no replacement; "
                    "synthesizing an unresolved replace task",
                    dep,
                )
                filtered.append(
                    MigrationTask(
                        kind="replace",
                        rationale=(
                            "Tiered r3: this package is deprecated, abandoned, or "
                            "superseded, so no same-package upgrade resolves the "
                            "finding. No replacement dependency was named by the "
                            "planner -- one still needs to be chosen."
                        ),
                    ).model_dump()
                )
            plan["tasks"] = filtered
            plan["tier_hint"] = "r3"
        else:
            filtered = [task for task in tasks if task.get("kind") != "replace"]
            if len(filtered) != len(tasks):
                logger.warning(
                    "_enforce_tier: dropped %d replace task(s) from tier %s "
                    "plan for %s (only an r3 verdict justifies abandoning the "
                    "package)",
                    len(tasks) - len(filtered),
                    tier or "untiered",
                    dep,
                )
            plan["tasks"] = filtered

        current_range = target.get("current_range")
        if not current_range:
            continue
        for task in plan["tasks"]:
            if task.get("kind") == "bump" and task.get("to_range") == current_range:
                logger.warning(
                    "_enforce_tier: bump task for %s targets its own "
                    "current_range %r; clearing to_range (reinstalling the "
                    "version already declared changes nothing)",
                    dep,
                    current_range,
                )
                task["to_range"] = None


async def build_plans_for_targets(
    targets: dict[str, dict], investigations: dict[str, dict]
) -> dict[str, dict]:
    """One structured-output call covering every given target at once, so
    the model can reason about cross-target `requires` coupling in a single
    pass instead of per-target tool calls. Returns {} for an empty input."""
    if not targets:
        return {}
    structured = _llm.with_structured_output(
        MigrationPlanBatch, method="function_calling"
    )
    try:
        batch = cast(
            MigrationPlanBatch,
            await structured.ainvoke(
                [
                    {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _format_targets(targets, investigations),
                    },
                ]
            ),
        )
    except Exception as exc:
        logger.warning(
            "build_plans_for_targets: planning call failed for %d target(s): %s",
            len(targets),
            exc,
        )
        return {}
    plans = {
        plan.target_dep: plan.model_dump()
        for plan in batch.plans
        if plan.target_dep in targets
    }
    _apply_release_digest(plans, investigations)
    _enforce_tier(plans, targets)
    return plans


async def build_migration_plan_node(
    state: RemediationState, config: RunnableConfig
) -> dict:
    targets = state.get("targets") or {}
    if not targets:
        return {"migration_plans": {}}
    investigations = state.get("investigations") or {}
    plans = await build_plans_for_targets(targets, investigations)
    return {"migration_plans": plans}
