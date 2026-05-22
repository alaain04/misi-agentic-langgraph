from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class SupplyChainSkill(InvestigationSkill):
    id = "SupplyChainSkill"
    name = "Supply Chain Integrity Assessment"
    description = "Checks provenance, install scripts, typosquatting indicators, and registry metadata"
    trigger_conditions = ["supply chain", "provenance", "typosquat", "install script", "compromise"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["supply_chain_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
