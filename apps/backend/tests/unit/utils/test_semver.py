from src.utils.semver import (
    is_noop_range_change,
    is_semver_major_bump,
    max_semver,
    parse_semver,
    range_floor,
)


def test_parse_semver_extracts_major_minor_patch():
    assert parse_semver("4.17.19") == (4, 17, 19)


def test_parse_semver_ignores_prerelease_suffix():
    assert parse_semver("4.17.19-beta.1") == (4, 17, 19)


def test_parse_semver_rejects_compound_string():
    assert parse_semver("3.2.19, 4.1.9") is None


def test_parse_semver_rejects_non_numeric():
    assert parse_semver("unstable") is None


def test_is_semver_major_bump_true_across_major_line():
    assert is_semver_major_bump("3.10.1", "4.17.19") is True


def test_is_semver_major_bump_false_within_same_major():
    assert is_semver_major_bump("4.17.15", "4.17.19") is False


def test_is_semver_major_bump_none_when_no_fix():
    assert is_semver_major_bump("1.3.0", None) is None


def test_is_semver_major_bump_none_when_unparseable():
    assert is_semver_major_bump("unstable", "2.0.0") is None


def test_max_semver_picks_numerically_highest_not_lexicographic():
    # Lexicographic comparison would wrongly pick "4.17.9" over "4.17.21".
    assert max_semver(["4.17.9", "4.17.21", "4.17.12"]) == "4.17.21"


def test_max_semver_ignores_unparseable_entries():
    assert max_semver(["3.2.19, 4.1.9", "4.17.21", "unknown"]) == "4.17.21"


def test_max_semver_none_when_nothing_parses():
    assert max_semver(["unknown", "3.2.19, 4.1.9"]) is None


def test_max_semver_empty_list():
    assert max_semver([]) is None


def test_is_noop_range_change_true_for_identical_pinned_range():
    assert is_noop_range_change("0.7.0", "0.7.0") is True


def test_is_noop_range_change_true_for_identical_caret_range():
    assert is_noop_range_change("^1.2.3", "^1.2.3") is True


def test_is_noop_range_change_false_for_real_upgrade():
    assert is_noop_range_change("^4.17.11", "^4.17.21") is False


def test_is_noop_range_change_false_for_widening_pin_to_caret():
    # "^1.2.3" can resolve higher than the pinned "1.2.3" -- only the
    # registry's latest_version could prove otherwise, so never call it a
    # no-op here.
    assert is_noop_range_change("1.2.3", "^1.2.3") is False


def test_is_noop_range_change_false_when_base_is_not_semver():
    assert is_noop_range_change("*", "*") is False
    assert is_noop_range_change("latest", "latest") is False


def test_is_noop_range_change_false_on_missing_side():
    assert is_noop_range_change(None, "1.2.3") is False
    assert is_noop_range_change("1.2.3", None) is False


def test_range_floor_strips_caret_and_tilde():
    assert range_floor("^4.17.11") == (4, 17, 11)
    assert range_floor("~1.2.3") == (1, 2, 3)
    assert range_floor("0.7.0") == (0, 7, 0)


def test_range_floor_none_for_unparseable_range():
    assert range_floor(">=1.0.0 <2.0.0") is None
    assert range_floor("*") is None
    assert range_floor(None) is None
