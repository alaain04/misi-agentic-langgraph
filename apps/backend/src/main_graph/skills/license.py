import logging

from src.main_graph.skills.base import InvestigationSkill, SkillContext
from src.models.evidence import Evidence
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_SEVERITY_HIGH_CATEGORIES = {"restricted"}
_SEVERITY_HIGH_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.0", "LGPL-2.1"}
_SEVERITY_MEDIUM_CATEGORIES = {"reciprocal", "unknown"}
_SEVERITY_LOW_CATEGORIES = {"permissive", "notice"}


def _severity(category: str, license_name: str) -> str:
    cat = category.lower()
    if cat in _SEVERITY_HIGH_CATEGORIES or license_name in _SEVERITY_HIGH_LICENSES:
        return "high"
    if cat in _SEVERITY_MEDIUM_CATEGORIES:
        return "medium"
    return "low"


class LicenseSkill(InvestigationSkill):
    id = "LicenseSkill"
    name = "License Compliance Assessment"
    description = "Checks license compatibility and copyleft obligations"
    trigger_conditions = ["license", "commercial use", "copyleft", "compliance"]
    required_inputs = ["repo_path"]
    evidence_kinds = ["license_signal"]

    async def execute(self, ctx: SkillContext) -> list[Evidence]:
        if not ctx.repo_path:
            return []

        container = ctx.services.get("container")
        if container is None:
            return []

        try:
            scan_data, _ = await run_trivy(
                container, ctx.repo_path,
                "--format", "json", "--scanners", "license",
            )
        except Exception:
            logger.exception("LicenseSkill: Trivy scan failed for %s", ctx.dep_name)
            return []

        findings = [
            lic
            for result in scan_data.get("Results", [])
            for lic in (result.get("Licenses") or [])
            if lic.get("PkgName") == ctx.dep_name
        ]

        evidence = []
        for lic in findings:
            category = lic.get("Category", "unknown")
            license_name = lic.get("Name", "")
            sev = _severity(category, license_name)
            risky = sev in ("high", "medium")
            evidence.append(Evidence(
                kind="license_signal",
                dep_name=ctx.dep_name,
                skill_id=self.id,
                hypothesis_id=ctx.hypothesis_id,
                signal=f"License {license_name} ({category}) detected for {ctx.dep_name}",
                raw_data=lic,
                source="trivy-license-scan",
                reliability=0.9,
                confidence=0.85 if sev == "high" else 0.6,
                supports_hypothesis=risky,
                severity=sev,
            ))

        return evidence
