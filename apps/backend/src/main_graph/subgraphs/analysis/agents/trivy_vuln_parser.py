"""Deterministic extraction of findings from `trivy fs --scanners vuln
--format json` output (Results[].Vulnerabilities[])."""

from __future__ import annotations

from src.models.conductor import EvidenceRef, FindingNote

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "UNKNOWN": "info",
}
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def parse_trivy_vuln_findings(
    trivy_output: dict, min_severity: str = "high"
) -> list[FindingNote]:
    """Convert a trivy_vuln_scan output into findings at or above
    `min_severity`, most severe first."""
    threshold = _rank(min_severity)
    findings: list[FindingNote] = []
    for result in (trivy_output or {}).get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            severity = _SEVERITY_MAP.get(vuln.get("Severity", "UNKNOWN"), "info")
            if _rank(severity) < threshold:
                continue
            installed = vuln.get("InstalledVersion", "unknown")
            fixed = vuln.get("FixedVersion") or "no fix available"
            vuln_id = vuln.get("VulnerabilityID", "unknown")
            findings.append(
                FindingNote(
                    dep_name=vuln.get("PkgName", "unknown"),
                    severity=severity,
                    description=(
                        f"{vuln.get('Title') or vuln_id}. "
                        f"{vuln.get('Description', '')} "
                        f"Installed {installed}; fixed in {fixed}."
                    ),
                    evidence=[
                        EvidenceRef(
                            tool="trivy",
                            url=vuln.get("PrimaryURL") or None,
                            log_snippet=(
                                f"{vuln_id}: severity={vuln.get('Severity')}; "
                                f"installed={installed}; fixed={fixed}"
                            ),
                        )
                    ],
                )
            )
    findings.sort(key=lambda f: _rank(f.severity), reverse=True)
    return findings
