"""License compliance analysis node — runs its own Trivy license scan."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.dao import (
    license_compliance_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.models import (
    LicenseComplianceEntry,
    LicenseRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.state import (
    LicenseComplianceState,
)
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)

_RISKY_CATEGORIES = {"restricted", "reciprocal", "unknown"}
_RISKY_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.0", "LGPL-2.1"}


def _risk_level(category: str, license_name: str) -> str:
    cat = category.lower()
    if cat in _RISKY_CATEGORIES or license_name in _RISKY_LICENSES:
        return "high"
    if cat in {"notice", "permissive"}:
        return "low"
    return "medium"


def _is_compliant(category: str, license_name: str) -> bool:
    return category.lower() != "restricted" and license_name not in _RISKY_LICENSES


async def analyze(state: LicenseComplianceState) -> dict:
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")

    if not repo_path:
        logger.error("license_compliance: no repo_path in state")
        entry = LicenseComplianceEntry(records=[], total_violations=0, concern=concern)
        result_id = await license_compliance_dao.save(entry)
        return {"result_id": result_id}

    scan_data: dict = {}
    try:
        logger.info("license_compliance: running Trivy license scan on %s", repo_path)
        scan_data, _ = await run_trivy(repo_path, "--format", "json", "--scanners", "license")
    except Exception as exc:  # noqa: BLE001
        logger.exception("license_compliance: Trivy scan failed: %s", exc)

    raw_licenses = [
        lic
        for result in scan_data.get("Results", [])
        for lic in (result.get("Licenses") or [])
    ]

    records = [
        LicenseRecord(
            name=lic.get("PkgName", ""),
            version="",
            license=lic.get("Name") or None,
            is_compliant=_is_compliant(lic.get("Category", "unknown"), lic.get("Name", "")),
            risk_level=_risk_level(lic.get("Category", "unknown"), lic.get("Name", "")),
            notes=f"category={lic.get('Category', 'unknown')}",
        )
        for lic in raw_licenses
        if lic.get("PkgName")
    ]

    entry = LicenseComplianceEntry(
        records=records,
        total_violations=sum(1 for r in records if not r.is_compliant),
        concern=concern,
    )
    result_id = await license_compliance_dao.save(entry)
    logger.info(
        "license_compliance: %d records, %d violations, result_id=%s",
        len(records), entry.total_violations, result_id,
    )
    return {"result_id": result_id}
