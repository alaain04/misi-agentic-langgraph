from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.license_data import LICENSES, resolve


def test_resolve_exact_id_returns_curated_entry():
    resolved = resolve("MIT")
    assert resolved == ("MIT", LICENSES["MIT"])


def test_resolve_unknown_id_returns_none():
    assert resolve("WTFPL") is None


def test_resolve_empty_or_none_returns_none():
    assert resolve("") is None
    assert resolve(None) is None


def test_resolve_see_license_in_file_returns_none():
    assert resolve("SEE LICENSE IN LICENSE.txt") is None


def test_resolve_or_picks_first_known_side():
    assert resolve("MIT OR Apache-2.0") == ("MIT", LICENSES["MIT"])


def test_resolve_or_falls_back_to_second_side():
    assert resolve("Foo-Bar OR MIT") == ("MIT", LICENSES["MIT"])


def test_resolve_or_unknown_when_neither_side_known():
    assert resolve("Foo OR Bar") is None


def test_resolve_and_combines_both_sides_most_restrictive():
    resolved = resolve("MIT AND Apache-2.0")
    assert resolved is not None
    key, entry = resolved
    assert key == "MIT AND Apache-2.0"
    assert entry.category == "permissive"
    assert entry.sublicense == "can"
    assert (
        entry.state_changes == "must"
    )  # Apache-2.0's must wins over MIT's not_required


def test_resolve_and_unknown_if_either_side_unknown():
    assert resolve("MIT AND Foo") is None


def test_resolve_rejects_nested_parenthesized_expression():
    assert resolve("(MIT OR Apache-2.0) AND GPL-3.0-only") is None


def test_gpl_3_0_only_is_strong_copyleft_with_same_license_must():
    entry = LICENSES["GPL-3.0-only"]
    assert entry.category == "strong_copyleft"
    assert entry.same_license == "must"


def test_agpl_3_0_only_is_network_copyleft():
    assert LICENSES["AGPL-3.0-only"].category == "network_copyleft"


def test_unlicensed_sentinel_is_proprietary_and_grants_nothing():
    entry = LICENSES["UNLICENSED"]
    assert entry.category == "proprietary"
    assert entry.sublicense == "cannot"
    assert entry.commercial_use == "cannot"
