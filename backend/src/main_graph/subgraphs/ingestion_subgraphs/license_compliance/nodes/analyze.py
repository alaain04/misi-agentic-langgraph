"""License compliance analysis node — parses Trivy scan output."""

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
    concern = state.get("concern", "")
    upstream = state.get("upstream_results", {})
    trivy_doc = upstream.get("sbom_gen", {})

    raw_licenses: list[dict] = trivy_doc.get("licenses", [])

    records: list[LicenseRecord] = []
    for lic in raw_licenses:
        pkg = lic.get("pkg_name", "")
        license_name = lic.get("license_name", "")
        category = lic.get("category", "unknown")
        if not pkg:
            continue
        records.append(
            LicenseRecord(
                name=pkg,
                version="",
                license=license_name or None,
                is_compliant=_is_compliant(category, license_name),
                risk_level=_risk_level(category, license_name),
                notes=f"category={category}",
            )
        )

    entry = LicenseComplianceEntry(
        records=records,
        total_violations=sum(1 for r in records if not r.is_compliant),
        concern=concern,
    )
    result_id = await license_compliance_dao.save(entry)
    logger.info(
        "license_compliance: %d records, %d violations, result_id=%s",
        len(records),
        entry.total_violations,
        result_id,
    )
    return {"result_id": result_id}
