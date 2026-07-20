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
