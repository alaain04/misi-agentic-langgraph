"""Deterministic extraction of findings from `trivy fs --scanners vuln
--format json` output (Results[].Vulnerabilities[])."""

from __future__ import annotations

from src.models.conductor import EvidenceRef, FindingNote
from src.utils.semver import is_semver_major_bump, max_semver

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


def _merge_group(dep_name: str, group: list[FindingNote]) -> FindingNote:
    """Collapse multiple per-CVE findings on one package into a single
    FindingNote: most severe wins as the headline severity, fixed_version is
    the highest fix version in the group -- since Trivy's FixedVersion is
    always for this same PkgName, upgrading to it resolves every CVE in the
    group, not just the one that reported it. Evidence from every CVE is
    kept so nothing is lost."""
    ranked = sorted(group, key=lambda f: _rank(f.severity), reverse=True)
    installed = next((f.installed_version for f in group if f.installed_version), None)
    fixed = max_semver([f.fixed_version for f in group if f.fixed_version])
    summary = "\n".join(f"- [{f.severity}] {f.description}" for f in ranked)
    installed_note = f" (installed {installed})" if installed else ""
    return FindingNote(
        dep_name=dep_name,
        severity=ranked[0].severity,
        description=(
            f"{len(group)} known vulnerabilities affect {dep_name}"
            f"{installed_note}:\n{summary}"
        ),
        evidence=[ev for f in ranked for ev in f.evidence],
        installed_version=installed,
        fixed_version=fixed,
        is_semver_major=is_semver_major_bump(installed, fixed),
    )


def _group_by_dep(findings: list[FindingNote]) -> list[FindingNote]:
    order: list[str] = []
    groups: dict[str, list[FindingNote]] = {}
    for f in findings:
        if f.dep_name not in groups:
            groups[f.dep_name] = []
            order.append(f.dep_name)
        groups[f.dep_name].append(f)
    return [
        groups[dep][0] if len(groups[dep]) == 1 else _merge_group(dep, groups[dep])
        for dep in order
    ]


def parse_trivy_vuln_findings(
    trivy_output: dict, min_severity: str = "high"
) -> list[FindingNote]:
    """Convert a trivy_vuln_scan output into findings at or above
    `min_severity`, most severe first. Multiple CVEs against the same
    package collapse into one FindingNote (see `_merge_group`) so
    remediation and report enrichment act on it once, not once per CVE."""
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
                    is_semver_major=is_semver_major_bump(installed_raw, fixed_raw),
                )
            )
    grouped = _group_by_dep(findings)
    grouped.sort(key=lambda f: _rank(f.severity), reverse=True)
    return grouped
