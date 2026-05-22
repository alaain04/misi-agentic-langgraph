from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class LicenseSkill(InvestigationSkill):
    id = "LicenseSkill"
    name = "License Compliance Assessment"
    description = "Checks license compatibility and copyleft obligations"
    trigger_conditions = ["license", "commercial use", "copyleft", "compliance"]
    required_inputs = ["repo_path"]
    evidence_kinds = ["license_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
