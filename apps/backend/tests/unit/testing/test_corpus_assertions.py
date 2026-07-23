from __future__ import annotations

from src.testing.corpus_assertions import evaluate, is_manual, validate_manifest_entry


def _f(
    dep_name: str,
    severity: str = "high",
    is_direct: bool = True,
    direct_dependents: list[str] | None = None,
    description: str = "",
    recommendation: str = "",
    business_impact: str = "",
) -> dict:
    return {
        "dep_name": dep_name,
        "severity": severity,
        "is_direct": is_direct,
        "direct_dependents": direct_dependents or [],
        "description": description,
        "recommendation": recommendation,
        "business_impact": business_impact,
    }


def _report(*findings: dict) -> dict:
    return {"findings": list(findings)}


# ---------------------------------------------------------------------------
# superset
# ---------------------------------------------------------------------------


def test_superset_pass():
    expectations = {
        "superset": [
            {
                "dep_name": "lodash",
                "is_direct": True,
                "severity_min": "high",
                "category": "vulnerability",
            }
        ]
    }
    report = _report(
        _f(
            "lodash",
            "critical",
            description="CVE-2019-10744 prototype pollution",
        )
    )
    assert evaluate(expectations, report) == []


def test_superset_fail_missing_dep():
    expectations = {"superset": [{"dep_name": "lodash"}]}
    report = _report(_f("express"))
    failures = evaluate(expectations, report)
    assert len(failures) == 1
    assert "lodash" in failures[0]
    assert "not present" in failures[0]


def test_superset_fail_severity_floor():
    expectations = {"superset": [{"dep_name": "lodash", "severity_min": "high"}]}
    report = _report(_f("lodash", severity="low"))
    failures = evaluate(expectations, report)
    assert len(failures) == 1
    assert "below floor high" in failures[0]


def test_superset_fail_directness_mismatch():
    expectations = {"superset": [{"dep_name": "minimist", "is_direct": False}]}
    report = _report(_f("minimist", is_direct=True))
    failures = evaluate(expectations, report)
    assert len(failures) == 1
    assert "is_direct" in failures[0]


def test_superset_fail_category_mismatch():
    expectations = {"superset": [{"dep_name": "gpl-dep", "category": "license"}]}
    report = _report(
        _f("gpl-dep", description="CVE-2024-0001 prototype pollution vulnerability")
    )
    failures = evaluate(expectations, report)
    assert len(failures) == 1
    assert "license" in failures[0]


def test_superset_direct_dependents_contains_pass():
    expectations = {
        "superset": [
            {"dep_name": "minimist", "direct_dependents_contains": ["mkdirp"]}
        ]
    }
    report = _report(_f("minimist", direct_dependents=["mkdirp", "optimist"]))
    assert evaluate(expectations, report) == []


def test_superset_direct_dependents_contains_fail():
    expectations = {
        "superset": [
            {"dep_name": "minimist", "direct_dependents_contains": ["mkdirp"]}
        ]
    }
    report = _report(_f("minimist", direct_dependents=["optimist"]))
    failures = evaluate(expectations, report)
    assert len(failures) == 1
    assert "missing" in failures[0]


def test_superset_direct_dependents_min_pass():
    expectations = {"superset": [{"dep_name": "minimist", "direct_dependents_min": 2}]}
    report = _report(_f("minimist", direct_dependents=["mkdirp", "optimist"]))
    assert evaluate(expectations, report) == []


def test_superset_direct_dependents_min_fail():
    expectations = {"superset": [{"dep_name": "minimist", "direct_dependents_min": 2}]}
    report = _report(_f("minimist", direct_dependents=["mkdirp"]))
    failures = evaluate(expectations, report)
    assert len(failures) == 1
    assert "< min 2" in failures[0]


def test_superset_passes_if_any_matching_finding_satisfies_all_constraints():
    expectations = {"superset": [{"dep_name": "lodash", "severity_min": "high"}]}
    report = _report(_f("lodash", severity="low"), _f("lodash", severity="critical"))
    assert evaluate(expectations, report) == []


# ---------------------------------------------------------------------------
# exactly_zero
# ---------------------------------------------------------------------------


def test_exactly_zero_pass():
    assert evaluate({"exactly_zero": True}, _report()) == []


def test_exactly_zero_fail():
    failures = evaluate({"exactly_zero": True}, _report(_f("x")))
    assert len(failures) == 1
    assert "expected zero findings" in failures[0]
    assert "x" in failures[0]


# ---------------------------------------------------------------------------
# not_flagged
# ---------------------------------------------------------------------------


def test_not_flagged_pass():
    expectations = {"not_flagged": ["lodash.merge"]}
    assert evaluate(expectations, _report(_f("other-dep"))) == []


def test_not_flagged_fail():
    expectations = {"not_flagged": ["lodash.merge"]}
    failures = evaluate(expectations, _report(_f("lodash.merge")))
    assert len(failures) == 1
    assert "lodash.merge" in failures[0]
    assert "was flagged" in failures[0]


# ---------------------------------------------------------------------------
# manual
# ---------------------------------------------------------------------------


def test_manual_never_gates():
    expectations = {"manual": "verify by hand"}
    assert evaluate(expectations, _report(_f("uuidv4"))) == []
    assert is_manual(expectations) is True


def test_is_manual_false_for_other_modes():
    assert is_manual({"exactly_zero": True}) is False


# ---------------------------------------------------------------------------
# manifest entry validation
# ---------------------------------------------------------------------------


def test_validate_manifest_entry_valid():
    entry = {
        "name": "misi-e2e-validation-clean",
        "repo_url": "https://github.com/owner/misi-e2e-validation-clean",
        "concern": "any dependency risk",
        "assert": {"exactly_zero": True},
    }
    assert validate_manifest_entry(entry) == []


def test_validate_manifest_entry_missing_required_key():
    entry = {
        "name": "misi-e2e-validation-clean",
        "concern": "any dependency risk",
        "assert": {"exactly_zero": True},
    }
    errors = validate_manifest_entry(entry)
    assert len(errors) == 1
    assert "repo_url" in errors[0]


def test_validate_manifest_entry_no_assert_block():
    entry = {"name": "x", "repo_url": "y", "concern": "z"}
    errors = validate_manifest_entry(entry)
    assert len(errors) == 1
    assert "assert" in errors[0]


def test_validate_manifest_entry_multiple_assert_modes():
    entry = {
        "name": "x",
        "repo_url": "y",
        "concern": "z",
        "assert": {"exactly_zero": True, "manual": "also this"},
    }
    errors = validate_manifest_entry(entry)
    assert len(errors) == 1
    assert "exactly one" in errors[0]
