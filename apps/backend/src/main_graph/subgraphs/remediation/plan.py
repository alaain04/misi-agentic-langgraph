"""Planning phase (spec 2026-08-08-remediation-flatten-planning-execution,
D1): a deterministic node that emits every target's MigrationPlan via ONE
batched structured-output call, replacing the old plan_and_orchestrate
deepagent's planning half. Nothing here dispatches an agent -- planning is a
single decision made from investigate_node's already-gathered evidence."""

from __future__ import annotations

import logging
from typing import cast

from langchain_core.runnables import RunnableConfig

from src.main_graph.subgraphs.remediation.state import RemediationState
from src.models.remediation import MigrationPlanBatch, MigrationTask
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_PLAN_SYSTEM_PROMPT = """\
You plan dependency remediation for a Node.js project. You are given a list \
of targets, each with a tier hint, a release digest (whether a migration is \
needed and a guide), its local dependents, and its call sites.

For EACH target produce exactly one MigrationPlan with target_dep matching \
the target exactly. The tier hint decides the SHAPE of the plan and \
overrides the release digest:
- tier_hint=r3: the package is deprecated, abandoned, or superseded, so a \
same-package upgrade is not a fix at all. The plan MUST contain exactly one \
`replace` task and MUST NOT contain a `bump` task -- not even when the \
release digest says migration_needed is false (for an abandoned package \
that only means there are no newer releases to break anything).
  Name a concrete replacement_dep ONLY if you are confident it is a real, \
currently-maintained npm package that covers this dependency's use in this \
project. Put your reasoning in the task's rationale: what the replacement \
is, why it fits these call sites, and roughly what the migration involves. \
If you cannot name one you are confident exists, leave replacement_dep null \
and use the rationale to say what kind of package would be needed. An \
honest "no candidate" is required -- a plausible-sounding package name that \
does not exist is worse than nothing, because a human will act on it.
- tier_hint=r1 or r2 with migration_needed=false: a single `bump` task.
- tier_hint=r1 or r2 with migration_needed=true: a `bump` task plus \
`codemod` task(s). Give the codemod task the specific files from call_sites \
that need review.

Set a `bump` task's to_range from the release digest's to_version. When \
to_version is unknown, propose the newest range you are confident resolves \
STRICTLY HIGHER than current_range. Never emit a bump whose to_range is \
current_range itself: reinstalling the version already declared changes \
nothing and is not a remediation. If you cannot name a higher version, say \
so in the rationale rather than restating current_range.

Set the plan's migration_guide to the release digest's guide VERBATIM -- \
leave it empty when migration_needed is false. Do not write your own \
commentary in its place.

If a target's fix requires bumping a companion dependency together with it \
(a peer dependency, a version lockstep), list that companion's name in \
`requires` -- even if the companion is not itself one of the targets given; \
it will be resolved separately. Do not invent a requirement without a \
concrete reason from the evidence given."""


def _format_targets(
    targets: dict[str, dict], investigations: dict[str, dict]
) -> str:
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


def _enforce_tier(plans: dict[str, dict], targets: dict[str, dict]) -> None:
    """Deterministically reconcile each plan with its target's tier, in
    place. The prompt asks for this, but a prompt is not a guarantee -- an
    r3 plan carrying a `bump` task shipped a bump of an abandoned package to
    its own installed version (job 6a7773a7576d0efd7796aa8c, `matcha`
    0.7.0 -> 0.7.0), which can only ever be a no-op. classify's r3 verdict
    is binding here, so the persisted plan can never contradict its own
    tier_hint again."""
    for dep, plan in plans.items():
        if (targets.get(dep) or {}).get("tier") != "r3":
            continue
        tasks = plan.get("tasks") or []
        kept = [t for t in tasks if t.get("kind") != "bump"]
        if len(kept) != len(tasks):
            logger.warning(
                "_enforce_tier: dropped %d bump task(s) from r3 plan for %s "
                "(a deprecated/superseded package has no same-package fix)",
                len(tasks) - len(kept),
                dep,
            )
        if not any(t.get("kind") == "replace" for t in kept):
            logger.warning(
                "_enforce_tier: r3 plan for %s named no replacement; "
                "synthesizing an unresolved replace task",
                dep,
            )
            kept.append(
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
        plan["tasks"] = kept
        plan["tier_hint"] = "r3"


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
