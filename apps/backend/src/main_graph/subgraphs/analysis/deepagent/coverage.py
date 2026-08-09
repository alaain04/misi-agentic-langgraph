"""Deterministic coverage guarantee for the analysis deep agent (spec D5, D8).

The deep agent decides HOW to investigate; whether every direct dependency
got looked at by a package-scoped agent is never left to its judgment --
UNLESS a whole-tree scan that already ran fully addresses the concern (see
whole_tree_scan_satisfies_concern below), in which case per-package coverage
of the rest would add nothing.
"""

from __future__ import annotations

import logging
import textwrap
from typing import cast

from pydantic import BaseModel, Field

from src.main_graph.subgraphs.analysis.agents.registry import (
    REGISTRY,
    get_agents,
)
from src.utils.model_registry import AgentRole, get_role_llm

logger = logging.getLogger(__name__)

WHOLE_TREE_AGENT_TYPES: set[str] = {"vulnerability_agent", "license_agent"}
"""Agents that scan the entire dependency tree in one run -- a second
dispatch adds no coverage and is capped to one run/job (D8), and they never
count toward direct-dependency coverage in compute_missing_direct_deps."""

PACKAGE_SCOPED_AGENT_TYPES: set[str] = set(REGISTRY) - WHOLE_TREE_AGENT_TYPES


def compute_missing_direct_deps(
    agent_calls: list[dict], direct_deps: list[str]
) -> list[str]:
    """Direct deps with no package-scoped AgentCallRecord covering them.

    agent_calls: list of AgentCallRecord.model_dump()-shaped dicts
    (agent_type, packages_to_focus, ...). Order of the returned list follows
    direct_deps, not agent_calls.
    """
    covered: set[str] = set()
    for call in agent_calls:
        if call.get("agent_type") in PACKAGE_SCOPED_AGENT_TYPES:
            covered.update(call.get("packages_to_focus") or [])
    return [dep for dep in direct_deps if dep not in covered]


_llm = get_role_llm(AgentRole.COVERAGE_JUDGE)

_COVERAGE_JUDGE_SYSTEM = textwrap.dedent("""\
    You decide whether a dependency-risk investigation has already fully
    addressed the user's concern.

    Some specialist scanners examine the ENTIRE dependency tree in a single
    run (every direct and transitive dependency at once). These whole-tree
    scanners have already completed SUCCESSFULLY for this job:
    {roster}

    Decide whether those whole-tree scans ALONE fully address the user's
    concern. Answer true only when investigating the remaining dependencies
    one-by-one would add nothing the concern asks for. If the concern also
    touches anything those scanners do not cover (e.g. maintenance/outdatedness,
    supply-chain/typosquatting, or open-ended web research), answer false.
    """).strip()


class _CoverageJudgment(BaseModel):
    fully_addressed: bool = Field(
        description=(
            "True if the whole-tree scans already run fully address the "
            "user's concern, so per-package investigation of the remaining "
            "dependencies would add nothing."
        )
    )
    reason: str = Field(description="One short sentence justifying the decision.")


async def whole_tree_scan_satisfies_concern(
    concern: str, ran_whole_tree_agents: list[str]
) -> bool:
    """LLM judgment: do the whole-tree scans that already completed
    successfully fully address `concern`, making per-package coverage of the
    remaining direct deps unnecessary?

    Returns False (keep requiring per-package coverage) when no whole-tree
    scan ran, the concern is empty, or the model call fails -- the
    conservative choice, since a spurious False only costs extra coverage,
    never missed coverage.
    """
    if not concern.strip() or not ran_whole_tree_agents:
        return False
    descriptions = {k: v.description for k, v in get_agents().items()}
    roster = "\n".join(
        f"- {a}: {descriptions.get(a, '')}" for a in sorted(ran_whole_tree_agents)
    )
    system = _COVERAGE_JUDGE_SYSTEM.format(roster=roster)
    structured = _llm.with_structured_output(
        _CoverageJudgment, method="function_calling"
    )
    try:
        judgment = cast(
            _CoverageJudgment,
            await structured.ainvoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"User concern: {concern}"},
                ]
            ),
        )
    except Exception:
        logger.warning(
            "whole_tree_scan_satisfies_concern: LLM judgment failed; "
            "falling back to per-package coverage",
            exc_info=True,
        )
        return False
    return judgment.fully_addressed
