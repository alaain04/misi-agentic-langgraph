from __future__ import annotations

from src.utils.determinism import (
    count_stats,
    dep_name_sets,
    finding_tuple_sets,
    jaccard,
    mean_pairwise_jaccard,
    summarize,
    unstable_dep_names,
)


def _f(dep_name: str, severity: str = "high") -> dict:
    return {"dep_name": dep_name, "severity": severity}


def test_jaccard_identical_is_one():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_is_zero():
    assert jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_partial():
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


def test_jaccard_both_empty_is_one():
    assert jaccard(set(), set()) == 1.0


def test_jaccard_one_empty_is_zero():
    assert jaccard({"a"}, set()) == 0.0


def test_mean_pairwise_single_set_is_one():
    assert mean_pairwise_jaccard([{"a"}]) == 1.0


def test_mean_pairwise_identical_sets_is_one():
    assert mean_pairwise_jaccard([{"a", "b"}, {"a", "b"}, {"a", "b"}]) == 1.0


def test_mean_pairwise_averages_pairs():
    # pairs: (AB,AB)=1.0, (AB,AC)=1/3, (AB,AC)=1/3 -> mean = (1 + 1/3 + 1/3)/3
    result = mean_pairwise_jaccard([{"a", "b"}, {"a", "b"}, {"a", "c"}])
    assert abs(result - (1 + 1 / 3 + 1 / 3) / 3) < 1e-9


def test_dep_name_and_tuple_sets():
    runs = [[_f("a", "high"), _f("b", "low")], [_f("a", "medium")]]
    assert dep_name_sets(runs) == [{"a", "b"}, {"a"}]
    assert finding_tuple_sets(runs) == [
        {("a", "high"), ("b", "low")},
        {("a", "medium")},
    ]


def test_count_stats():
    runs = [[_f("a")], [_f("a"), _f("b")], [_f("a"), _f("b"), _f("c")]]
    stats = count_stats(runs)
    assert stats == {"min": 1, "mean": 2.0, "max": 3}


def test_count_stats_empty_runs():
    assert count_stats([]) == {"min": 0, "mean": 0.0, "max": 0}


def test_unstable_dep_names_all_stable_is_empty():
    runs = [[_f("a"), _f("b")], [_f("a"), _f("b")]]
    assert unstable_dep_names(runs) == set()


def test_unstable_dep_names_flags_partial_presence():
    runs = [[_f("a"), _f("b")], [_f("a")], [_f("a"), _f("b")]]
    # b appears in 2 of 3 runs -> unstable
    assert unstable_dep_names(runs) == {"b"}


def test_summarize_integration():
    runs = [[_f("a"), _f("b")], [_f("a"), _f("b")], [_f("a")]]
    s = summarize(runs)
    assert s["runs"] == 3
    assert s["count"] == {"min": 1, "mean": 5 / 3, "max": 2}
    assert 0.0 < s["dep_name_jaccard"] < 1.0
    assert s["unstable_dep_names"] == ["b"]


def test_extract_findings_prefers_report_artifact():
    from scripts.determinism_check import extract_findings

    resp = {
        "artifacts": [
            {"node": "analysis"},
            {
                "node": "report",
                "output": {"findings": [{"dep_name": "x", "severity": "high"}]},
            },
        ],
        "results": {"report_result_id": "abc"},
    }
    assert extract_findings(resp) == [{"dep_name": "x", "severity": "high"}]


def test_extract_findings_falls_back_to_results():
    from scripts.determinism_check import extract_findings

    resp = {
        "artifacts": [{"node": "report", "output": {}}],
        "results": {
            "analysis_report": {"findings": [{"dep_name": "y", "severity": "low"}]}
        },
    }
    assert extract_findings(resp) == [{"dep_name": "y", "severity": "low"}]


def test_extract_findings_empty_when_absent():
    from scripts.determinism_check import extract_findings

    assert extract_findings({"artifacts": [], "results": {}}) == []
