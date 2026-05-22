from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


class ReleaseAnomalySkill(InvestigationSkill):
    id = "ReleaseAnomalySkill"
    name = "Release Anomaly Detection"
    description = "Detects suspicious release patterns: rapid publishing, version gaps, ownership changes"
    trigger_conditions = ["release", "version", "publish", "anomaly", "typosquat"]
    required_inputs = ["dep_name"]
    evidence_kinds = ["release_anomaly"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        return []
