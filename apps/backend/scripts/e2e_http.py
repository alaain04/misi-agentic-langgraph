"""Shared HTTP plumbing for driving one analysis job to completion.

Used by scripts/corpus_check.py to submit/poll multiple fixtures in a single
run. Unlike scripts/e2e_check.py's private helpers (which sys.exit on
failure — fine for a single-job CLI), these functions always return status
info so a multi-fixture runner can continue past one broken fixture and
report on all of them.
"""

from __future__ import annotations

import json
import time
import urllib.request


def request(base_url: str, method: str, path: str, body: dict | None = None) -> dict:
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def submit(base_url: str, repo_url: str, concern: str, autopilot: bool = True) -> str:
    resp = request(
        base_url,
        "POST",
        "/analyze",
        {"repo_url": repo_url, "concern": concern, "autopilot": autopilot},
    )
    return resp["trace_id"]


def poll(
    base_url: str,
    trace_id: str,
    until: set[str] | None = None,
    every: int = 15,
    timeout: int = 900,
) -> dict:
    """Poll /analyze/{trace_id} until status is in `until` or a terminal
    failure occurs. Always RETURNS the final response rather than exiting —
    callers decide how to treat failed/cancelled/timed-out runs. On timeout,
    returns a synthetic {"status": "timeout", "trace_id": trace_id}.
    """
    until = until or {"done"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = request(base_url, "GET", f"/analyze/{trace_id}")
        status = resp["status"]
        if status in until or status in {"failed", "cancelled"}:
            return resp
        time.sleep(every)
    return {"status": "timeout", "trace_id": trace_id}


def extract_report(status_resp: dict) -> dict:
    """Report lives in artifacts[node=='report'].output — NOT `results`,
    which only ever carries {"report_result_id": ...} (see job_runner.py).
    """
    for artifact in status_resp.get("artifacts", []):
        if artifact.get("node") == "report":
            return artifact.get("output") or {}
    return {}
