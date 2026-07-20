"""Pure finding-set determinism metrics.

A "run" is a list of finding dicts (each with at least `dep_name` and
`severity`). Given N runs of the same repo+concern, these functions quantify
how stable the finding set is across runs. No I/O, no backend, no LLM — the
CLI driver (scripts/determinism_check.py) collects the runs and calls
summarize().
"""

from __future__ import annotations

import itertools


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity |a n b| / |a u b|. Two empty sets are identical (1.0)."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def mean_pairwise_jaccard(sets: list[set]) -> float:
    """Mean Jaccard over every unordered pair. Fewer than two sets => 1.0."""
    if len(sets) < 2:
        return 1.0
    pairs = list(itertools.combinations(sets, 2))
    return sum(jaccard(a, b) for a, b in pairs) / len(pairs)


def dep_name_sets(runs: list[list[dict]]) -> list[set[str]]:
    return [{f["dep_name"] for f in run} for run in runs]


def finding_tuple_sets(runs: list[list[dict]]) -> list[set[tuple[str, str]]]:
    return [{(f["dep_name"], f["severity"]) for f in run} for run in runs]


def count_stats(runs: list[list[dict]]) -> dict:
    counts = [len(r) for r in runs]
    if not counts:
        return {"min": 0, "mean": 0.0, "max": 0}
    return {"min": min(counts), "mean": sum(counts) / len(counts), "max": max(counts)}


def unstable_dep_names(runs: list[list[dict]]) -> set[str]:
    """dep_names present in some runs but not all (the source of instability)."""
    sets = dep_name_sets(runs)
    if not sets:
        return set()
    union: set[str] = set().union(*sets)
    intersection: set[str] = set(sets[0]).intersection(*sets[1:])
    return union - intersection


def summarize(runs: list[list[dict]]) -> dict:
    """Full determinism report for N runs of the same input."""
    return {
        "runs": len(runs),
        "count": count_stats(runs),
        "dep_name_jaccard": mean_pairwise_jaccard(dep_name_sets(runs)),
        "finding_tuple_jaccard": mean_pairwise_jaccard(finding_tuple_sets(runs)),
        "unstable_dep_names": sorted(unstable_dep_names(runs)),
    }
