"""Deterministic extraction of findings from `trivy fs --scanners vuln
--format json` output (Results[].Vulnerabilities[])."""

from __future__ import annotations

import re

from src.models.conductor import EvidenceRef, FindingNote

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "UNKNOWN": "info",
}
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_SEMVER_RE = re.compile(r"^(\d+)\.\d+\.\d+")


def _rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def _major_version(version: str) -> int | None:
    match = _SEMVER_RE.match(version.strip())
    return int(match.group(1)) if match else None


def _is_semver_major(installed: str | None, fixed: str | None) -> bool | None:
    """Same-dependency-upgrade comparison only - Trivy's InstalledVersion/
    FixedVersion are always for the same PkgName, so this has no meaning for
    a package replacement/migration. None means not computable: no fix
    available, or either version string isn't parseable as semver."""
    if not installed or not fixed:
        return None
    installed_major = _major_version(installed)
    fixed_major = _major_version(fixed)
    if installed_major is None or fixed_major is None:
        return None
    return installed_major != fixed_major


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
            installed_raw = vuln.get("InstalledVersion") or None
            fixed_raw = vuln.get("FixedVersion") or None
            installed = installed_raw or "unknown"
            fixed = fixed_raw or "no fix available"
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
                    installed_version=installed_raw,
                    fixed_version=fixed_raw,
                    is_semver_major=_is_semver_major(installed_raw, fixed_raw),
                )
            )
    findings.sort(key=lambda f: _rank(f.severity), reverse=True)
    return findings
