from __future__ import annotations

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence


def _compute_blast_radius_from_sbom(dep_name: str, sbom: dict) -> dict:
    dependencies = sbom.get("dependencies", [])
    direct = [
        entry["ref"]
        for entry in dependencies
        if dep_name in entry.get("dependsOn", [])
    ]
    direct_set = set(direct)
    transitive = [
        entry["ref"]
        for entry in dependencies
        if entry["ref"] not in direct_set
        and any(d in entry.get("dependsOn", []) for d in direct)
        and entry["ref"] != dep_name
    ]
    return {"direct_dependents": direct, "transitive_dependents": transitive}


class BlastRadiusSkill(InvestigationSkill):
    id = "BlastRadiusSkill"
    name = "Blast Radius Estimation"
    description = "Computes transitive dependents and graph depth to estimate change impact"
    trigger_conditions = ["blast radius", "transitive", "impact", "fanout", "graph"]
    required_inputs = ["dep_name", "sbom"]
    evidence_kinds = ["blast_radius_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        if not ctx.dep_name or not ctx.sbom:
            return []

        result = _compute_blast_radius_from_sbom(ctx.dep_name, ctx.sbom)
        direct = result.get("direct_dependents", [])
        transitive = result.get("transitive_dependents", [])
        total = len(direct) + len(transitive)
        is_high = total >= 5

        signal = (
            f"{ctx.dep_name} affects {len(direct)} direct and {len(transitive)} transitive packages"
            if total > 0
            else f"{ctx.dep_name} has no dependents in the project graph"
        )

        return [Evidence(
            kind="blast_radius_signal",
            dep_name=ctx.dep_name,
            skill_id=self.id,
            hypothesis_id=ctx.hypothesis_id,
            signal=signal,
            raw_data=result,
            source="sbom_graph",
            reliability=0.9,
            confidence=0.85,
            severity="high" if is_high else "low",
            supports_hypothesis=is_high,
        )]
