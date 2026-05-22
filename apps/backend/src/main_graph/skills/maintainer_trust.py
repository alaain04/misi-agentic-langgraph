from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class MaintainerTrustSkill(InvestigationSkill):
    id = "MaintainerTrustSkill"
    name = "Maintainer Trust Analysis"
    description = "Evaluates maintainer activity, commit patterns, and issue responsiveness"
    trigger_conditions = ["abandoned", "maintainer", "activity", "bus factor"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["maintainer_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
