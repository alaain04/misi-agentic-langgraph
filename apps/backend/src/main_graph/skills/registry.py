from src.main_graph.skills.blast_radius import BlastRadiusSkill
from src.main_graph.skills.ecosystem import EcosystemSkill
from src.main_graph.skills.license import LicenseSkill
from src.main_graph.skills.maintainer_trust import MaintainerTrustSkill
from src.main_graph.skills.reachability import ReachabilitySkill
from src.main_graph.skills.release_anomaly import ReleaseAnomalySkill
from src.main_graph.skills.supply_chain import SupplyChainSkill
from src.main_graph.skills.vulnerability import VulnerabilitySkill
from src.main_graph.skills.base import InvestigationSkill

SKILL_REGISTRY: dict[str, InvestigationSkill] = {
    skill.id: skill
    for skill in [
        VulnerabilitySkill(),
        MaintainerTrustSkill(),
        SupplyChainSkill(),
        LicenseSkill(),
        ReachabilitySkill(),
        BlastRadiusSkill(),
        ReleaseAnomalySkill(),
        EcosystemSkill(),
    ]
}

SKILL_DESCRIPTIONS: dict[str, str] = {
    sid: f"{s.name}: {s.description} | triggers: {', '.join(s.trigger_conditions)}"
    for sid, s in SKILL_REGISTRY.items()
}
