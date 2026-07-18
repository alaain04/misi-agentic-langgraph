"""Deterministic extraction of findings from `npm/pnpm audit --json` output.

The audit already scans the entire dependency tree in one run, so we take the
whole result rather than sampling packages. Two output shapes exist:
- pnpm / npm v6: top-level "advisories" keyed by advisory id.
- npm v7+: top-level "vulnerabilities" keyed by package name.

`audit --json` does not honour --audit-level for filtering, so the severity
gate is applied here.
"""

from __future__ import annotations

from src.models.conductor import EvidenceRef, FindingNote

_SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "moderate": 2,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def _rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def _normalize(severity: str) -> str:
    """Map npm's `moderate` onto the report vocabulary (`medium`)."""
    return "medium" if severity == "moderate" else severity


def parse_audit_findings(
    audit_output: dict, min_severity: str = "high"
) -> list[FindingNote]:
    """Convert audit output into findings at or above `min_severity`, most severe
    first."""
    output = audit_output or {}
    threshold = _rank(min_severity)
    if "advisories" in output:
        findings = _from_advisories(output["advisories"], threshold)
    elif "vulnerabilities" in output:
        findings = _from_vulnerabilities(output["vulnerabilities"], threshold)
    else:
        findings = []
    findings.sort(key=lambda f: _rank(f.severity), reverse=True)
    return findings


def _from_advisories(advisories: dict, threshold: int) -> list[FindingNote]:
    findings = []
    for adv in (advisories or {}).values():
        severity = adv.get("severity", "info")
        if _rank(severity) < threshold:
            continue
        installed = (
            ", ".join(f.get("version", "?") for f in adv.get("findings", []))
            or "unknown"
        )
        vulnerable = adv.get("vulnerable_versions", "?")
        patched = adv.get("patched_versions", "?")
        cves = ", ".join(adv.get("cves") or []) or "none"
        findings.append(
            FindingNote(
                dep_name=adv.get("module_name", "unknown"),
                severity=_normalize(severity),
                description=(
                    f"{adv.get('title', 'Known vulnerability')}. "
                    f"Installed {installed} is within the vulnerable range "
                    f"{vulnerable}; patched in {patched}. CVEs: {cves}."
                ),
                evidence=[
                    EvidenceRef(
                        tool="npm_audit",
                        url=adv.get("url"),
                        log_snippet=(
                            f"severity={severity}; vulnerable={vulnerable}; "
                            f"patched={patched}; installed={installed}"
                        ),
                    )
                ],
            )
        )
    return findings


def _from_vulnerabilities(vulnerabilities: dict, threshold: int) -> list[FindingNote]:
    findings = []
    for name, entry in (vulnerabilities or {}).items():
        severity = entry.get("severity", "info")
        if _rank(severity) < threshold:
            continue
        advisories = [v for v in entry.get("via", []) if isinstance(v, dict)]
        title = advisories[0].get("title") if advisories else "Vulnerable dependency"
        url = advisories[0].get("url") if advisories else None
        vulnerable = entry.get("range", "?")
        fix = _fix_note(entry.get("fixAvailable"))
        findings.append(
            FindingNote(
                dep_name=name,
                severity=_normalize(severity),
                description=f"{title}. Affected range {vulnerable}. {fix}",
                evidence=[
                    EvidenceRef(
                        tool="npm_audit",
                        url=url,
                        log_snippet=(
                            f"severity={severity}; range={vulnerable}; "
                            f"fixAvailable={entry.get('fixAvailable')}"
                        ),
                    )
                ],
            )
        )
    return findings


def _fix_note(fix) -> str:
    if fix is True:
        return "A compatible fix is available."
    if isinstance(fix, dict):
        breaking = fix.get("isSemVerMajor")
        return (
            f"Fix requires {fix.get('name')}@{fix.get('version')} "
            f"(breaking change: {breaking})."
        )
    return "No fix currently available."
