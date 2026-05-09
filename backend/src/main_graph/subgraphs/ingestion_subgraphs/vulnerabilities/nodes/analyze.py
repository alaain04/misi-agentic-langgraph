"""Vulnerabilities analysis node — parses Trivy scan output."""

import logging

from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.dao import (
    vulnerabilities_dao,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.models import (
    VulnerabilitiesEntry,
    VulnerabilityFinding,
    VulnerabilityRecord,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.state import (
    VulnerabilitiesState,
)

logger = logging.getLogger(__name__)


def _severity_rank(s: str) -> int:
    return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s.upper(), 0)


def _build_records(trivy_vulns: list[dict]) -> list[VulnerabilityRecord]:
    by_pkg: dict[str, list[VulnerabilityFinding]] = {}
    versions: dict[str, str] = {}

    for v in trivy_vulns:
        pkg = v.get("pkg_name", "")
        if not pkg:
            continue
        versions.setdefault(pkg, v.get("installed_version", "unknown"))
        by_pkg.setdefault(pkg, []).append(
            VulnerabilityFinding(
                cve_id=v.get("vulnerability_id") or None,
                severity=v.get("severity", "UNKNOWN"),
                description=v.get("description") or None,
                fixed_in=v.get("fixed_version") or None,
            )
        )

    records: list[VulnerabilityRecord] = []
    for pkg, findings in by_pkg.items():
        max_sev = max(findings, key=lambda f: _severity_rank(f.severity)).severity
        records.append(
            VulnerabilityRecord(
                name=pkg,
                version=versions[pkg],
                findings=findings,
                risk_level=max_sev.lower(),
            )
        )
    return records


async def analyze(state: VulnerabilitiesState) -> dict:
    concern = state.get("concern", "")
    upstream = state.get("upstream_results", {})
    trivy_doc = upstream.get("sbom_gen", {})

    raw_vulns: list[dict] = trivy_doc.get("vulnerabilities", [])
    records = _build_records(raw_vulns)

    entry = VulnerabilitiesEntry(
        records=records,
        total_findings=sum(len(r.findings) for r in records),
        concern=concern,
    )
    result_id = await vulnerabilities_dao.save(entry)
    logger.info(
        "vulnerabilities: %d packages with findings, result_id=%s",
        len(records),
        result_id,
    )
    return {"result_id": result_id}
