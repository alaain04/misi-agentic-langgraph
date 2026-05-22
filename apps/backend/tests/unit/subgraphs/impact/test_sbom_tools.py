import pytest

from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.sbom_tools import (
    _pkg_name,
    compute_blast_radius,
    compute_direct_dependents,
)

_SBOM = {
    "components": [
        {"name": "express", "version": "4.18.2"},
        {"name": "body-parser", "version": "1.20.1"},
        {"name": "cookie", "version": "0.5.0"},
        {"name": "myapp", "version": "1.0.0"},
    ],
    "dependencies": [
        {
            "ref": "myapp@1.0.0",
            "dependsOn": ["express@4.18.2", "cookie@0.5.0"],
        },
        {
            "ref": "express@4.18.2",
            "dependsOn": ["body-parser@1.20.1", "cookie@0.5.0"],
        },
        {
            "ref": "body-parser@1.20.1",
            "dependsOn": [],
        },
        {
            "ref": "cookie@0.5.0",
            "dependsOn": [],
        },
    ],
}


def test_compute_direct_dependents_returns_direct_parents():
    result = compute_direct_dependents("body-parser", _SBOM)
    assert result == ["express"]


def test_compute_direct_dependents_multiple_parents():
    result = compute_direct_dependents("cookie", _SBOM)
    assert sorted(result) == ["express", "myapp"]


def test_compute_direct_dependents_no_parents():
    result = compute_direct_dependents("myapp", _SBOM)
    assert result == []


def test_compute_blast_radius_single_level():
    result = compute_blast_radius("body-parser", _SBOM)
    assert result["direct_dependents"] == 1  # express
    assert result["transitive_dependents"] == 2  # express + myapp
    assert result["max_depth"] == 2  # body-parser → express (depth 1) → myapp (depth 2)


def test_compute_blast_radius_leaf_dep():
    result = compute_blast_radius("cookie", _SBOM)
    assert result["direct_dependents"] == 2  # express + myapp
    assert result["transitive_dependents"] == 2
    assert result["max_depth"] == 1


def test_compute_blast_radius_not_found():
    result = compute_blast_radius("unknown-pkg", _SBOM)
    assert result["direct_dependents"] == 0
    assert result["transitive_dependents"] == 0
    assert result["max_depth"] == 0


def test_compute_blast_radius_empty_sbom():
    result = compute_blast_radius("express", {})
    assert result["direct_dependents"] == 0


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("express@4.18.2", "express"),
        ("@types/react@18.0.0", "@types/react"),
        ("pkg:npm/express@4.18.2", "express"),
        ("pkg:npm/@scope/pkg@1.0.0", "@scope/pkg"),
        ("express", "express"),
    ],
)
def test_pkg_name_extracts_name(ref, expected):
    assert _pkg_name(ref) == expected
