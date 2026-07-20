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


def _unstable(sets: list[set]) -> set:
    """Elements present in some sets but not all (union minus intersection)."""
    if not sets:
        return set()
    return set().union(*sets) - set(sets[0]).intersection(*sets[1:])


def unstable_dep_names(runs: list[list[dict]]) -> set[str]:
    """dep_names present in some runs but not all (the source of instability)."""
    return _unstable(dep_name_sets(runs))


def unstable_finding_tuples(runs: list[list[dict]]) -> set[tuple[str, str]]:
    """(dep_name, severity) tuples present in some runs but not all.

    Catches instability the dep-name diff misses — e.g. a package found in
    every run but whose severity flips between runs (dep_name stable, tuple not).
    """
    return _unstable(finding_tuple_sets(runs))


def all_runs_empty(runs: list[list[dict]]) -> bool:
    """True when every run found nothing. In that case the Jaccard scores are a
    misleading 1.0 (empty-vs-empty), which reads as 'perfectly stable' but may
    just mean 'reliably found nothing' or 'every run failed'. Callers must
    surface this rather than trust the 1.0."""
    return len(runs) > 0 and all(len(r) == 0 for r in runs)


def summarize(runs: list[list[dict]]) -> dict:
    """Full determinism report for N runs of the same input."""
    return {
        "runs": len(runs),
        "count": count_stats(runs),
        "dep_name_jaccard": mean_pairwise_jaccard(dep_name_sets(runs)),
        "finding_tuple_jaccard": mean_pairwise_jaccard(finding_tuple_sets(runs)),
        "unstable_dep_names": sorted(unstable_dep_names(runs)),
        "unstable_finding_tuples": sorted(unstable_finding_tuples(runs)),
        "all_runs_empty": all_runs_empty(runs),
    }
