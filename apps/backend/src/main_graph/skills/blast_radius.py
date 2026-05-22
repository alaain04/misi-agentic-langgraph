from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class BlastRadiusSkill(InvestigationSkill):
    id = "BlastRadiusSkill"
    name = "Blast Radius Estimation"
    description = "Computes transitive dependents and graph depth to estimate change impact"
    trigger_conditions = ["blast radius", "transitive", "impact", "fanout", "graph"]
    required_inputs = ["dep_name", "sbom"]
    evidence_kinds = ["blast_radius_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
