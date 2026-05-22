from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class EcosystemSkill(InvestigationSkill):
    id = "EcosystemSkill"
    name = "Ecosystem Reputation Analysis"
    description = "Assesses npm download trends, community health, and package popularity signals"
    trigger_conditions = ["popularity", "downloads", "community", "ecosystem", "reputation"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["ecosystem_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
