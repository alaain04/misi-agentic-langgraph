#!/usr/bin/env python3
"""Drive one full E2E analysis cycle and validate success criteria."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from urllib.error import URLError

BASE = "http://localhost:8001"
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "none": -1}


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
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--timeout", type=int, default=600, help="Per-phase timeout in seconds")
    args = parser.parse_args()

    global BASE
    BASE = args.base_url

    print(f"\n=== E2E: {args.repo} ===")
    print(f"Concern : {args.concern}\n")

    # 1. Start analysis
    resp = _request("POST", "/analyze", {"repo_url": args.repo, "concern": args.concern})
    trace_id = resp["trace_id"]
    print(f"trace_id: {trace_id}\n")

    # 2. Wait for HITL gate 1 (plan)
    print("[1] Waiting for plan proposal (HITL gate 1)...")
    resp = _poll(trace_id, {"awaiting_approval"}, timeout=args.timeout)
    planner = next((a for a in resp.get("artifacts", []) if a["node"] == "investigation_planner"), None)
    if planner and planner.get("messages"):
        print("Plan preview:")
        print(planner["messages"][0]["content"][:600])
        print("...\n")

    # 3. Approve plan
    print("[2] Approving plan...")
    _request("POST", f"/analyze/{trace_id}/chat",
             {"message": "Yes, looks good. Please proceed with the full investigation."})

    # 4. Wait for HITL gate 2 or completion
    print("\n[3] Waiting for skill execution to complete...")
    resp = _poll(trace_id, {"awaiting_approval", "done"}, timeout=args.timeout)

    if resp["status"] == "awaiting_approval":
        reviewer = next((a for a in resp.get("artifacts", []) if a["node"] == "finding_reviewer"), None)
        if reviewer and reviewer.get("messages"):
            print("Findings preview:")
            print(reviewer["messages"][0]["content"][:500])
            print()

        # 5. Acknowledge findings
        print("[4] Acknowledging findings (HITL gate 2)...")
        _request("POST", f"/analyze/{trace_id}/chat",
                 {"message": "Acknowledged. Please generate the final report."})

        print("\n[5] Waiting for final report...")
        resp = _poll(trace_id, {"done"}, timeout=args.timeout)

    # 6. Evaluate
    results = resp.get("results") or {}
    report = results.get("analysis_report") or {}
    findings = report.get("findings") or []
    artifact_nodes = [a["node"] for a in resp.get("artifacts", [])]

    print("\n=== REPORT ===")
    print(f"overall_risk_level : {report.get('overall_risk_level', 'MISSING')}")
    print(f"findings           : {len(findings)}")
    print(f"recommendations    : {report.get('recommendations', [])}")
    for f in findings:
        conf = f.get("confidence", 0)
        print(f"  [{f.get('severity','?').upper():8s}] {f.get('dep_name'):30s} score={f.get('risk_score'):4.1f}  conf={conf:.0%}")

    print(f"\nArtifacts tracked  : {artifact_nodes}")
    for node in ("evidence_collector", "evidence_correlator"):
        if node not in artifact_nodes:
            print(f"  WARNING: {node} artifact missing (Fix C not applied)")

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
