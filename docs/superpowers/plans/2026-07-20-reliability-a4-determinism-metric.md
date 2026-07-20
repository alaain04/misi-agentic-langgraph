# A4 — Determinism Metric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure how reproducible the analysis pipeline is — run the same repo+concern N times and report finding-set stability — so we have a number that says whether A1 was enough and whether A2 (LLM determinism levers) is worth doing.

**Architecture:** Pure metric functions in `src/utils/determinism.py` (Jaccard, count stats, unstable-finding diff, `summarize`), unit-tested in isolation with synthetic run sets. A thin CLI driver `scripts/determinism_check.py` drives N analyses against a running backend via the existing `GET /analyze/{trace_id}` API, extracts each run's findings, and prints/serializes the summary.

**Tech Stack:** Python 3.12, stdlib only for the metric (no new deps), pytest, ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-07-20-reliability-a4-determinism-metric-design.md`

## Global Constraints

- Package manager: `uv` (`uv run <cmd>`), never pip/bare python.
- No emoji in code, comments, or commit messages.
- Backend only — no `apps/frontend`.
- No new runtime dependencies — the metric uses only the Python stdlib (`itertools`).
- The metric functions must be **pure and unit-tested without a backend, DAO, or LLM**; only the CLI driver touches the network.
- Findings extraction must be robust: current API populates findings in the `report` artifact's `output`, not always in `results.analysis_report` — try the artifact first, fall back to `results`.
- Before claiming done: run `uv run pytest`, `uv run ruff check .`, `uv run mypy src` from `apps/backend` and show output.
- All commands below run from `apps/backend/`.

---

### Task 1: Pure determinism-metric functions

**Files:**
- Create: `src/utils/determinism.py`
- Test: `tests/unit/utils/test_determinism.py`

**Interfaces:**
- Input shape: a "run" is a `list[dict]`, each dict a finding with at least `dep_name: str` and `severity: str`. A run set is `list[list[dict]]` (N runs).
- Produces:
  - `jaccard(a: set, b: set) -> float`
  - `mean_pairwise_jaccard(sets: list[set]) -> float`
  - `dep_name_sets(runs: list[list[dict]]) -> list[set[str]]`
  - `finding_tuple_sets(runs: list[list[dict]]) -> list[set[tuple[str, str]]]`
  - `count_stats(runs: list[list[dict]]) -> dict`
  - `unstable_dep_names(runs: list[list[dict]]) -> set[str]`
  - `summarize(runs: list[list[dict]]) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/utils/test_determinism.py`:

```python
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
    assert finding_tuple_sets(runs) == [{("a", "high"), ("b", "low")}, {("a", "medium")}]


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/utils/test_determinism.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.utils.determinism'`.

- [ ] **Step 3: Implement the metric module**

Create `src/utils/determinism.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/utils/test_determinism.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/utils/determinism.py tests/unit/utils/test_determinism.py && uv run mypy src/utils/determinism.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/utils/determinism.py tests/unit/utils/test_determinism.py
git commit -m "feat: add pure determinism-metric functions"
```

---

### Task 2: CLI driver `scripts/determinism_check.py`

**Files:**
- Create: `scripts/determinism_check.py`
- Test: `tests/unit/utils/test_determinism.py` (append one test for the pure extraction helper)

**Interfaces:**
- Consumes: `summarize` from Task 1.
- Produces (importable pure helper, unit-tested): `extract_findings(status_resp: dict) -> list[dict]` — pulls findings from the `report` artifact's `output`, falling back to `results.analysis_report`.
- CLI: `uv run python scripts/determinism_check.py --repo <url> --concern <text> --runs N [--base-url http://localhost:8000] [--timeout 900] [--json <path>]`. Exit 0 on success, 2 if backend unreachable.

- [ ] **Step 1: Write the failing test for the extraction helper**

Append to `tests/unit/utils/test_determinism.py`:

```python
def test_extract_findings_prefers_report_artifact():
    from scripts.determinism_check import extract_findings

    resp = {
        "artifacts": [
            {"node": "analysis"},
            {"node": "report", "output": {"findings": [{"dep_name": "x", "severity": "high"}]}},
        ],
        "results": {"report_result_id": "abc"},
    }
    assert extract_findings(resp) == [{"dep_name": "x", "severity": "high"}]


def test_extract_findings_falls_back_to_results():
    from scripts.determinism_check import extract_findings

    resp = {
        "artifacts": [{"node": "report", "output": {}}],
        "results": {"analysis_report": {"findings": [{"dep_name": "y", "severity": "low"}]}},
    }
    assert extract_findings(resp) == [{"dep_name": "y", "severity": "low"}]


def test_extract_findings_empty_when_absent():
    from scripts.determinism_check import extract_findings

    assert extract_findings({"artifacts": [], "results": {}}) == []
```

Note: importing `scripts.determinism_check` requires `scripts/` to be importable. Confirm there is a `scripts/__init__.py`; if not, create an empty one in this task so the test can import the helper. (`scripts/e2e_check.py` is run as a file, not imported, so the package marker may be absent.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/utils/test_determinism.py -k extract_findings -v`
Expected: FAIL with import error (module/helper does not exist yet).

- [ ] **Step 3: Create the driver**

If `scripts/__init__.py` does not exist, create it empty. Then create `scripts/determinism_check.py`:

```python
#!/usr/bin/env python3
"""Run one repo+concern through the pipeline N times and report finding-set
stability. Requires a running backend (see e2e_check.py for the same request
pattern).

Usage:
    uv run python scripts/determinism_check.py \\
        --repo https://github.com/chalk/chalk \\
        --concern "outdated deps and known vulnerabilities" \\
        --runs 3 [--base-url http://localhost:8000] [--timeout 900] [--json out.json]

Exit codes:
    0  completed
    2  cannot reach backend
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from urllib.error import URLError

from src.utils.determinism import summarize

BASE = "http://localhost:8000"


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def extract_findings(status_resp: dict) -> list[dict]:
    """Findings live in the report artifact's output on the current API; fall
    back to results.analysis_report for older shapes."""
    for a in status_resp.get("artifacts", []):
        if a.get("node") == "report":
            findings = (a.get("output") or {}).get("findings")
            if findings is not None:
                return findings
    report = (status_resp.get("results") or {}).get("analysis_report") or {}
    return report.get("findings") or []


def _poll(trace_id: str, every: int = 15, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = _request("GET", f"/analyze/{trace_id}")
        status = resp["status"]
        if status == "done":
            return resp
        if status in {"failed", "cancelled"}:
            print(f"  run reached terminal state={status}")
            return resp
        time.sleep(every)
    print(f"  timed out after {timeout}s")
    return {"status": "timeout", "artifacts": [], "results": {}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure pipeline determinism")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--concern", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    global BASE
    BASE = args.base_url

    print(f"\n=== Determinism: {args.repo} x{args.runs} ===")
    print(f"Concern: {args.concern}\n")

    # Submit all N jobs first, then poll — the backend processes them concurrently.
    trace_ids: list[str] = []
    for i in range(args.runs):
        resp = _request(
            "POST",
            "/analyze",
            {"repo_url": args.repo, "concern": args.concern, "autopilot": True},
        )
        trace_ids.append(resp["trace_id"])
        print(f"submitted run {i + 1}/{args.runs}: {resp['trace_id']}")

    runs: list[list[dict]] = []
    for i, tid in enumerate(trace_ids):
        print(f"waiting on run {i + 1}/{args.runs} ({tid})...")
        status = _poll(tid, timeout=args.timeout)
        findings = extract_findings(status)
        runs.append(findings)
        print(f"  findings: {len(findings)}")

    report = summarize(runs)

    print("\n=== DETERMINISM REPORT ===")
    print(f"runs                 : {report['runs']}")
    print(f"finding count        : {report['count']}")
    print(f"dep-name jaccard     : {report['dep_name_jaccard']:.3f}")
    print(f"finding-tuple jaccard: {report['finding_tuple_jaccard']:.3f}")
    print(f"unstable dep_names   : {report['unstable_dep_names']}")

    if args.json_path:
        with open(args.json_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nwrote {args.json_path}")


if __name__ == "__main__":
    try:
        main()
    except URLError as exc:
        print(f"BLOCKED: cannot reach backend — {exc}")
        sys.exit(2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/utils/test_determinism.py -v`
Expected: PASS (all, including the 3 new extraction tests).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check scripts/determinism_check.py tests/unit/utils/test_determinism.py && uv run mypy scripts/determinism_check.py`
Expected: no errors. (If mypy is not configured to scan `scripts/`, run it on the file directly as shown; fix any type issues it reports.)

- [ ] **Step 6: Commit**

```bash
git add scripts/determinism_check.py scripts/__init__.py tests/unit/utils/test_determinism.py
git commit -m "feat: add determinism_check CLI driver over the analyze API"
```

---

### Task 3: Verification

**Files:** none.

- [ ] **Step 1: Full suite**

Run: `uv run pytest`
Expected: all pass (baseline + the new determinism tests).

- [ ] **Step 2: Lint + type-check the backend**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 3: Optional live baseline**

With a backend + MongoDB + Docker running, produce the post-A1 baseline number:

Run: `uv run python scripts/determinism_check.py --repo https://github.com/chalk/chalk --concern "outdated or unmaintained dependencies and known vulnerabilities" --runs 3 --json /tmp/chalk_determinism.json`
Expected: prints the report; record the dep-name Jaccard as the post-A1 baseline (expected near 1.0 now that duplication is fixed). This is the number that informs the A2 go/no-go. The unit tests are the real gate; this is a live confirmation.

- [ ] **Step 4: Commit any residual fixes**

```bash
git add -A
git commit -m "test: verify determinism metric across backend suite"
```

---

## Self-Review Notes

- **Spec coverage:** primary dep-name Jaccard + secondary finding-tuple Jaccard + count stats + symmetric-difference (`unstable_dep_names`) + JSON output → Task 1 (math) and Task 2 (driver). Fixture-backed mode is future (needs Workstream B); chalk bootstrap is Task 3 Step 3.
- **Purity/testability:** all metric functions and the extraction helper are pure and unit-tested; only the driver's submit/poll touches the network (validated manually, per the spec).
- **No new deps:** stdlib `itertools`/`urllib` only.
- **Type consistency:** `summarize(list[list[dict]]) -> dict` and `extract_findings(dict) -> list[dict]` used exactly as defined; the driver feeds `extract_findings` output (list of runs) into `summarize`.
