#!/usr/bin/env python3
"""Drive one full E2E analysis cycle and validate success criteria.

Usage:
    uv run python scripts/e2e_check.py \\
        --repo https://github.com/example/repo \\
        --concern "security vulnerabilities" \\
        [--base-url http://localhost:8000] \\
        [--timeout 900]

Exit codes:
    0  all criteria met
    1  criteria failures or job failed
    2  cannot reach backend
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from urllib.error import URLError

BASE = "http://localhost:8000"
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "none": -1}
_ARTIFACT_NODES = ("prep", "analysis", "report")


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _poll(trace_id: str, until: set[str], every: int = 15, timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = _request("GET", f"/analyze/{trace_id}")
        status = resp["status"]
        print(f"  status={status}")
        if status in until:
            return resp
        if status in {"failed", "cancelled"}:
            print(f"FAIL: job reached terminal state={status}")
            sys.exit(1)
        time.sleep(every)
    print(f"FAIL: timed out after {timeout}s waiting for {until}")
    sys.exit(1)


def _check_criteria(report: dict, findings: list[dict]) -> list[str]:
    failures: list[str] = []

    if not report.get("overall_risk_level"):
        failures.append("overall_risk_level missing or empty")

    high_risk = [f for f in findings if f.get("risk_score", 0) > 2.0]
    if high_risk and not report.get("recommendations"):
        failures.append(
            f"recommendations empty but {len(high_risk)} findings have risk_score > 2.0"
        )

    for f in findings:
        sev = f.get("severity", "low")
        score = f.get("risk_score", 0)
        if _SEVERITY_RANK.get(sev, 0) >= _SEVERITY_RANK["medium"] and score < 1.0:
            failures.append(
                f"{f['dep_name']}: severity={sev} but risk_score={score} — inconsistent"
            )

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one E2E analysis and check criteria")
    parser.add_argument("--repo", required=True, help="GitHub repo URL")
    parser.add_argument("--concern", required=True, help="User concern text")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=900, help="Per-phase timeout in seconds")
    args = parser.parse_args()

    global BASE
    BASE = args.base_url

    print(f"\n=== E2E: {args.repo} ===")
    print(f"Concern : {args.concern}\n")

    # 1. Start analysis (autopilot=True skips any HITL gates)
    resp = _request("POST", "/analyze", {
        "repo_url": args.repo,
        "concern": args.concern,
        "autopilot": True,
    })
    trace_id = resp["trace_id"]
    print(f"trace_id: {trace_id}\n")

    # 2. Wait for completion
    print("[1] Waiting for analysis to complete...")
    resp = _poll(trace_id, {"done"}, timeout=args.timeout)

    # 3. Evaluate
    results = resp.get("results") or {}
    report = results.get("analysis_report") or {}
    findings = report.get("findings") or []
    artifact_nodes = [a["node"] for a in resp.get("artifacts", [])]

    print("\n=== REPORT ===")
    print(f"overall_risk_level : {report.get('overall_risk_level', 'MISSING')}")
    print(f"findings           : {len(findings)}")
    print(f"recommendations    : {len(report.get('recommendations', []))}")
    for f in findings:
        print(f"  [{f.get('severity', '?').upper():8s}] {f.get('dep_name', '?'):30s}")

    print(f"\nArtifacts tracked  : {artifact_nodes}")
    missing = [n for n in _ARTIFACT_NODES if n not in artifact_nodes]
    if missing:
        print(f"  WARNING: missing artifact nodes: {missing}")

    failures = _check_criteria(report, findings)
    if failures:
        print(f"\nFAIL — {len(failures)} criteria failed:")
        for msg in failures:
            print(f"  - {msg}")
        sys.exit(1)

    print("\nPASS: all success criteria met")


if __name__ == "__main__":
    try:
        main()
    except URLError as exc:
        print(f"BLOCKED: cannot reach backend — {exc}")
        sys.exit(2)
