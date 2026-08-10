from src.main_graph.subgraphs.analysis.agents.trivy_vuln_parser import (
    parse_trivy_vuln_findings,
)


def _trivy_output(*vulns: dict) -> dict:
    return {
        "SchemaVersion": 2,
        "Results": [{"Target": "package-lock.json", "Vulnerabilities": list(vulns)}],
    }


def test_parses_high_and_above_by_default():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-2020-8203",
            "PkgName": "lodash",
            "InstalledVersion": "4.17.15",
            "FixedVersion": "4.17.19",
            "Severity": "HIGH",
            "Title": "prototype pollution",
            "Description": "details here",
            "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2020-8203",
        },
        {
            "VulnerabilityID": "CVE-LOW-1",
            "PkgName": "some-pkg",
            "InstalledVersion": "1.0.0",
            "FixedVersion": "1.0.1",
            "Severity": "LOW",
            "Title": "minor issue",
            "Description": "minor",
            "PrimaryURL": "https://example.com",
        },
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert len(findings) == 1
    assert findings[0].dep_name == "lodash"
    assert findings[0].severity == "high"
    assert "CVE-2020-8203" in findings[0].evidence[0].log_snippet
    assert findings[0].evidence[0].url == "https://avd.aquasec.com/nvd/cve-2020-8203"


def test_maps_critical_and_sorts_most_severe_first():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-A",
            "PkgName": "pkg-a",
            "InstalledVersion": "1.0.0",
            "FixedVersion": "1.0.1",
            "Severity": "HIGH",
            "Title": "a",
            "Description": "a",
            "PrimaryURL": "",
        },
        {
            "VulnerabilityID": "CVE-B",
            "PkgName": "pkg-b",
            "InstalledVersion": "2.0.0",
            "FixedVersion": None,
            "Severity": "CRITICAL",
            "Title": "b",
            "Description": "b",
            "PrimaryURL": "",
        },
    )
    findings = parse_trivy_vuln_findings(output, min_severity="low")
    assert [f.severity for f in findings] == ["critical", "high"]
    assert "no fix available" in findings[0].description


def test_empty_results_returns_no_findings():
    assert parse_trivy_vuln_findings({"SchemaVersion": 2, "Results": []}) == []


def test_missing_results_key_returns_no_findings():
    assert parse_trivy_vuln_findings({"SchemaVersion": 2}) == []


def test_major_bump_sets_is_semver_major_true():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-MAJOR",
            "PkgName": "lodash",
            "InstalledVersion": "3.10.1",
            "FixedVersion": "4.17.19",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "3.10.1"
    assert findings[0].fixed_version == "4.17.19"
    assert findings[0].is_semver_major is True


def test_minor_patch_bump_sets_is_semver_major_false():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-MINOR",
            "PkgName": "lodash",
            "InstalledVersion": "4.17.15",
            "FixedVersion": "4.17.19",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "4.17.15"
    assert findings[0].fixed_version == "4.17.19"
    assert findings[0].is_semver_major is False


def test_no_fix_available_leaves_semver_fields_none():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-NOFIX",
            "PkgName": "left-pad",
            "InstalledVersion": "1.3.0",
            "FixedVersion": None,
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "1.3.0"
    assert findings[0].fixed_version is None
    assert findings[0].is_semver_major is None


def test_unparseable_version_leaves_is_semver_major_none():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-WEIRD",
            "PkgName": "oddpkg",
            "InstalledVersion": "unstable",
            "FixedVersion": "2.0.0",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "unstable"
    assert findings[0].fixed_version == "2.0.0"
    assert findings[0].is_semver_major is None


def test_comma_separated_fixed_version_leaves_is_semver_major_none():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-MULTI",
            "PkgName": "multipkg",
            "InstalledVersion": "4.5.0",
            "FixedVersion": "3.2.19, 4.1.9",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].fixed_version == "3.2.19, 4.1.9"
    assert findings[0].is_semver_major is None


def test_multiple_cves_on_same_package_collapse_into_one_finding():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-A",
            "PkgName": "lodash",
            "InstalledVersion": "4.17.11",
            "FixedVersion": "4.17.12",
            "Severity": "CRITICAL",
            "Title": "prototype pollution in defaultsDeep",
            "Description": "d1",
            "PrimaryURL": "https://example.com/a",
        },
        {
            "VulnerabilityID": "CVE-B",
            "PkgName": "lodash",
            "InstalledVersion": "4.17.11",
            "FixedVersion": "4.17.21",
            "Severity": "HIGH",
            "Title": "command injection via template",
            "Description": "d2",
            "PrimaryURL": "https://example.com/b",
        },
        {
            "VulnerabilityID": "CVE-C",
            "PkgName": "lodash",
            "InstalledVersion": "4.17.11",
            "FixedVersion": "4.18.0",
            "Severity": "MEDIUM",
            "Title": "prototype pollution via array path",
            "Description": "d3",
            "PrimaryURL": "https://example.com/c",
        },
        {
            "VulnerabilityID": "CVE-D",
            "PkgName": "other-pkg",
            "InstalledVersion": "1.0.0",
            "FixedVersion": "1.0.1",
            "Severity": "HIGH",
            "Title": "unrelated",
            "Description": "d4",
            "PrimaryURL": "",
        },
    )
    findings = parse_trivy_vuln_findings(output, min_severity="low")

    assert len(findings) == 2
    lodash = next(f for f in findings if f.dep_name == "lodash")
    assert lodash.severity == "critical"  # most severe of the group
    assert lodash.installed_version == "4.17.11"
    # 4.18.0 numerically dominates 4.17.21/4.17.12 and resolves every CVE.
    assert lodash.fixed_version == "4.18.0"
    assert len(lodash.evidence) == 3
    assert {e.log_snippet.split(":")[0] for e in lodash.evidence} == {
        "CVE-A",
        "CVE-B",
        "CVE-C",
    }
    for title in (
        "prototype pollution in defaultsDeep",
        "command injection via template",
        "prototype pollution via array path",
    ):
        assert title in lodash.description


def test_single_finding_package_is_not_wrapped_by_grouping():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-SOLO",
            "PkgName": "solo-pkg",
            "InstalledVersion": "1.0.0",
            "FixedVersion": "1.0.1",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert len(findings) == 1
    assert findings[0].description == "t. d Installed 1.0.0; fixed in 1.0.1."
