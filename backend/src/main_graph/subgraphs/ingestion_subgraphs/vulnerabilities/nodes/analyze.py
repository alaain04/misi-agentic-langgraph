"""Vulnerabilities analysis node — runs its own Trivy vuln scan."""

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
from src.utils.trivy import run_trivy

logger = logging.getLogger(__name__)


def _severity_rank(s: str) -> int:
    return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s.upper(), 0)


def _build_records(raw_vulns: list[dict]) -> list[VulnerabilityRecord]:
    by_pkg: dict[str, list[VulnerabilityFinding]] = {}
    versions: dict[str, str] = {}

    for v in raw_vulns:
        pkg = v.get("PkgName", "")
        if not pkg:
            continue
        versions.setdefault(pkg, v.get("InstalledVersion", "unknown"))
        by_pkg.setdefault(pkg, []).append(
            VulnerabilityFinding(
                cve_id=v.get("VulnerabilityID") or None,
                severity=v.get("Severity", "UNKNOWN"),
                description=v.get("Description") or None,
                fixed_in=v.get("FixedVersion") or None,
            )
        )

    return [
        VulnerabilityRecord(
            name=pkg,
            version=versions[pkg],
            findings=findings,
            risk_level=max(findings, key=lambda f: _severity_rank(f.severity)).severity.lower(),
        )
        for pkg, findings in by_pkg.items()
    ]


async def analyze(state: VulnerabilitiesState) -> dict:
    repo_path = state.get("repo_path", "")
    concern = state.get("concern", "")

    if not repo_path:
        logger.error("vulnerabilities: no repo_path in state")
        entry = VulnerabilitiesEntry(records=[], total_findings=0, concern=concern)
        result_id = await vulnerabilities_dao.save(entry)
        return {"result_id": result_id}

    scan_data: dict = {}
    try:
        logger.info("vulnerabilities: running Trivy vuln scan on %s", repo_path)
        scan_data, _ = await run_trivy(repo_path, "--format", "json", "--scanners", "vuln")
    except Exception as exc:  # noqa: BLE001
        logger.exception("vulnerabilities: Trivy scan failed: %s", exc)

    raw_vulns = [
        v
        for result in scan_data.get("Results", [])
        for v in (result.get("Vulnerabilities") or [])
    ]

    records = _build_records(raw_vulns)
    entry = VulnerabilitiesEntry(
        records=records,
        total_findings=sum(len(r.findings) for r in records),
        concern=concern,
    )
    result_id = await vulnerabilities_dao.save(entry)
    logger.info("vulnerabilities: %d packages, result_id=%s", len(records), result_id)
    return {"result_id": result_id}
