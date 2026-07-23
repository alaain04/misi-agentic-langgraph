"""Pure ground-truth assertion evaluator for the E2E fixture corpus.

Each fixture's manifest entry declares an `assert` block of one of four
modes: `superset`, `exactly_zero`, `not_flagged`, `manual`. `evaluate()`
checks a live report against that block with no I/O, no backend, no LLM —
the CLI driver (scripts/corpus_check.py) submits/polls the backend and
calls evaluate() on the resulting report.
"""

from __future__ import annotations

from src.utils.severity import SEVERITY_ORDER

_REQUIRED_MANIFEST_KEYS = ("name", "repo_url", "concern")
_ASSERT_MODES = ("superset", "exactly_zero", "not_flagged", "manual")

# Category is inferred from finding TEXT because ReportFinding has no
# structured category field. Documented heuristic, matched against
# description+recommendation+business_impact, lowercased.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "vulnerability": (
        "cve",
        "ghsa",
        "advisory",
        "vulnerab",
        "prototype pollution",
        "redos",
        "exploit",
    ),
    "license": ("license", "gpl", "agpl", "copyleft", "spdx", "contagion"),
    "maintenance": (
        "unmaintained",
        "abandoned",
        "stale",
        "no releases",
        "last release",
        "deprecated",
        "inactive",
    ),
    "supply_chain": (
        "typosquat",
        "supply chain",
        "supply-chain",
        "malicious",
        "install script",
        "suspicious",
    ),
}


def _finding_text(finding: dict) -> str:
    return " ".join(
        [
            finding.get("description", ""),
            finding.get("recommendation", ""),
            finding.get("business_impact", ""),
        ]
    ).lower()


def _matches_category(finding: dict, category: str) -> bool:
    keywords = _CATEGORY_KEYWORDS.get(category, ())
    text = _finding_text(finding)
    return any(keyword in text for keyword in keywords)


def _find_by_dep(findings: list[dict], dep_name: str) -> list[dict]:
    return [f for f in findings if f.get("dep_name") == dep_name]


def _check_one_expected(spec: dict, findings: list[dict]) -> list[str]:
    """Failures for one expected-finding spec (empty = satisfied).

    A spec passes if AT LEAST ONE finding for `dep_name` satisfies every
    constraint on the spec — not every matching finding needs to.
    """
    dep_name = spec["dep_name"]
    matches = _find_by_dep(findings, dep_name)
    if not matches:
        return [f"expected finding for '{dep_name}' not present"]

    per_match_errors = []
    for finding in matches:
        errors: list[str] = []
        if "is_direct" in spec and bool(finding.get("is_direct")) != bool(
            spec["is_direct"]
        ):
            errors.append(
                f"{dep_name}: is_direct={finding.get('is_direct')} "
                f"expected {spec['is_direct']}"
            )
        if "severity_min" in spec:
            got = SEVERITY_ORDER.get(finding.get("severity", ""), -1)
            need = SEVERITY_ORDER.get(spec["severity_min"], 0)
            if got < need:
                errors.append(
                    f"{dep_name}: severity={finding.get('severity')} "
                    f"below floor {spec['severity_min']}"
                )
        direct_dependents = finding.get("direct_dependents") or []
        if "direct_dependents_contains" in spec:
            missing = [
                d
                for d in spec["direct_dependents_contains"]
                if d not in direct_dependents
            ]
            if missing:
                errors.append(
                    f"{dep_name}: direct_dependents {direct_dependents} "
                    f"missing {missing}"
                )
        if (
            "direct_dependents_min" in spec
            and len(direct_dependents) < spec["direct_dependents_min"]
        ):
            errors.append(
                f"{dep_name}: direct_dependents has {len(direct_dependents)} "
                f"< min {spec['direct_dependents_min']}"
            )
        if "category" in spec and not _matches_category(finding, spec["category"]):
            errors.append(
                f"{dep_name}: no '{spec['category']}' keywords in finding text"
            )
        if not errors:
            return []  # one fully-satisfying match => pass
        per_match_errors.append("; ".join(errors))
    return [
        f"{dep_name}: found but no match satisfied all constraints ({per_match_errors})"
    ]


def is_manual(expectations: dict) -> bool:
    return "manual" in expectations


def evaluate(expectations: dict, report: dict) -> list[str]:
    """Check `report` against a manifest entry's `assert` block.

    Returns human-readable failure strings; empty list == pass. `manual`
    mode never gates — it always returns [].
    """
    findings = report.get("findings") or []

    if is_manual(expectations):
        return []

    if expectations.get("exactly_zero"):
        if not findings:
            return []
        return [
            f"expected zero findings, got {len(findings)}: "
            f"{[f.get('dep_name') for f in findings]}"
        ]

    if "not_flagged" in expectations:
        flagged = {f.get("dep_name") for f in findings}
        return [
            f"'{dep}' was flagged but must not be"
            for dep in expectations["not_flagged"]
            if dep in flagged
        ]

    if "superset" in expectations:
        failures: list[str] = []
        for spec in expectations["superset"]:
            failures.extend(_check_one_expected(spec, findings))
        return failures

    return [f"manifest entry has no recognized assert mode: {list(expectations)}"]


def validate_manifest_entry(entry: dict) -> list[str]:
    """Structural checks on one manifest entry. Empty list == valid."""
    errors = [key for key in _REQUIRED_MANIFEST_KEYS if key not in entry]
    if errors:
        return [f"missing required key(s): {errors}"]
    assert_block = entry.get("assert")
    if not isinstance(assert_block, dict):
        return ["missing 'assert' block"]
    present_modes = [mode for mode in _ASSERT_MODES if mode in assert_block]
    if len(present_modes) != 1:
        return [
            f"'assert' block must have exactly one of {_ASSERT_MODES}, "
            f"found {present_modes}"
        ]
    if present_modes[0] == "superset":
        spec_errors: list[str] = []
        for spec in assert_block["superset"]:
            severity_min = spec.get("severity_min")
            if severity_min is not None and severity_min not in SEVERITY_ORDER:
                spec_errors.append(
                    f"{spec.get('dep_name')}: severity_min "
                    f"'{severity_min}' not in {sorted(SEVERITY_ORDER)}"
                )
            category = spec.get("category")
            if category is not None and category not in _CATEGORY_KEYWORDS:
                spec_errors.append(
                    f"{spec.get('dep_name')}: category '{category}' not in "
                    f"{sorted(_CATEGORY_KEYWORDS)}"
                )
        return spec_errors
    return []
