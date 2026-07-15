from __future__ import annotations

from src.main_graph.subgraphs.analysis.utils.audit_parser import parse_audit_findings

# pnpm / npm v6 shape
_ADVISORIES = {
    "advisories": {
        "1": {"module_name": "lodash", "severity": "high", "title": "Code injection",
              "vulnerable_versions": ">=4.0.0 <=4.17.23", "patched_versions": "<0.0.0",
              "cves": ["CVE-1"], "url": "https://x/1", "findings": [{"version": "4.17.21"}]},
        "2": {"module_name": "form-data", "severity": "critical", "title": "Unsafe random",
              "vulnerable_versions": "<2.5.4", "patched_versions": ">=2.5.4",
              "cves": [], "url": "https://x/2", "findings": [{"version": "2.5.0"}]},
        "3": {"module_name": "qs", "severity": "moderate", "title": "DoS",
              "vulnerable_versions": "<6.5.3", "patched_versions": ">=6.5.3",
              "cves": [], "url": "https://x/3", "findings": [{"version": "6.5.0"}]},
        "4": {"module_name": "tmp", "severity": "low", "title": "Symlink write",
              "vulnerable_versions": "<0.2.4", "patched_versions": ">=0.2.4",
              "cves": [], "url": "https://x/4", "findings": [{"version": "0.2.0"}]},
    }
}

# npm v7+ shape
_VULNERABILITIES = {
    "vulnerabilities": {
        "minimatch": {"name": "minimatch", "severity": "high", "range": "<3.0.5",
                      "via": [{"title": "ReDoS", "url": "https://x/m", "severity": "high"}],
                      "fixAvailable": True},
        "ejs": {"name": "ejs", "severity": "critical", "range": "<3.1.7",
                "via": [{"title": "Template injection", "url": "https://x/e", "severity": "critical"}],
                "fixAvailable": {"name": "ejs", "version": "3.1.10", "isSemVerMajor": False}},
        "moment": {"name": "moment", "severity": "moderate", "range": "<2.29.4",
                   "via": [{"title": "ReDoS", "url": "https://x/mo", "severity": "moderate"}],
                   "fixAvailable": True},
    }
}


def test_advisories_default_threshold_takes_high_and_above():
    findings = parse_audit_findings(_ADVISORIES)  # default high
    names = [f.dep_name for f in findings]
    assert names == ["form-data", "lodash"]  # critical first, moderate/low dropped
    assert findings[0].severity == "critical"


def test_advisories_medium_threshold_includes_moderate_as_medium():
    findings = parse_audit_findings(_ADVISORIES, min_severity="medium")
    by_name = {f.dep_name: f.severity for f in findings}
    assert by_name == {"form-data": "critical", "lodash": "high", "qs": "medium"}


def test_advisories_critical_threshold_only_critical():
    findings = parse_audit_findings(_ADVISORIES, min_severity="critical")
    assert [f.dep_name for f in findings] == ["form-data"]


def test_advisories_evidence_populated():
    findings = parse_audit_findings(_ADVISORIES, min_severity="critical")
    ev = findings[0].evidence[0]
    assert ev.tool == "npm_audit"
    assert ev.url == "https://x/2"
    assert "installed=2.5.0" in ev.log_snippet


def test_npm_v7_vulnerabilities_shape():
    findings = parse_audit_findings(_VULNERABILITIES, min_severity="high")
    names = [f.dep_name for f in findings]
    assert names == ["ejs", "minimatch"]  # critical first; moment (moderate) dropped
    assert "Fix requires ejs@3.1.10" in findings[0].description


def test_empty_or_errored_output_returns_nothing():
    assert parse_audit_findings({}) == []
    assert parse_audit_findings({"error": "npm audit failed"}) == []
    assert parse_audit_findings(None) == []
