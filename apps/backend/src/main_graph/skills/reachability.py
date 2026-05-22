from __future__ import annotations

import json
import logging

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.main_graph.skills.tools.filesystem import find_usages
from src.models.evidence import Evidence

logger = logging.getLogger(__name__)


class ReachabilitySkill(InvestigationSkill):
    id = "ReachabilitySkill"
    name = "Reachability Assessment"
    description = "Determines if a dependency is actually imported in execution paths"
    trigger_conditions = ["code impact", "unused", "tree shaking", "reachability"]
    required_inputs = ["repo_path", "dep_name"]
    evidence_kinds = ["reachability_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        if not ctx.repo_path or not ctx.dep_name:
            return []

        try:
            raw = find_usages.invoke({"dep_name": ctx.dep_name, "repo_path": ctx.repo_path})
            usages = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            logger.exception("ReachabilitySkill: find_usages failed for %s", ctx.dep_name)
            return []

        is_used = len(usages) > 0
        return [Evidence(
            kind="reachability_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            signal=(
                f"{ctx.dep_name} is imported in {len(usages)} location(s)"
                if is_used
                else f"{ctx.dep_name} not found in any import — dependency appears unreachable"
            ),
            raw_data={"usages": usages},
            source="ast_scan",
            reliability=0.8,
            confidence=0.8 if is_used else 0.75,
            severity="info",
            supports_hypothesis=not is_used,
        )]
