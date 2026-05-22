from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class ReachabilitySkill(InvestigationSkill):
    id = "ReachabilitySkill"
    name = "Reachability Assessment"
    description = "Determines if a dependency is actually imported and used in execution paths"
    trigger_conditions = ["code impact", "unused", "tree shaking", "reachability"]
    required_inputs = ["repo_path", "dep_name"]
    evidence_kinds = ["reachability_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
