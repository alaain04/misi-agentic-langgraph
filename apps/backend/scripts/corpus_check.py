#!/usr/bin/env python3
"""Run the E2E fixture corpus and assert each fixture's ground-truth findings.

All corpus repos (apps/backend/tests/e2e/corpus.yaml) are PRIVATE. clone_repo
does anonymous `git clone` with no auth support today (Workstream D1 — PAT
read for private repos — has not landed), so this script cannot actually
reach any of them yet. By default every entry prints SKIP and the run exits
0, so the corpus is fully wired and ready to assert the moment D1 lands —
just set CORPUS_PAT_AVAILABLE=1 (or pass --assert-live), no code change.

Usage:
    uv run python scripts/corpus_check.py \\
        [--manifest tests/e2e/corpus.yaml] \\
        [--base-url http://localhost:8000] \\
        [--assert-live] \\
        [--timeout 900] \\
        [--only misi-e2e-validation-clean]

Exit codes:
    0  no FAIL rows (SKIP/MANUAL/PASS all fine)
    1  at least one FAIL row
    2  cannot reach backend
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.error import URLError

import yaml

from scripts.e2e_http import extract_report, poll, submit
from src.testing.corpus_assertions import evaluate, is_manual, validate_manifest_entry

_REASON_NO_PAT = "requires PAT — Workstream D1"


def load_manifest(path: str) -> list[dict]:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    entries = data.get("repos") or []
    for entry in entries:
        errors = validate_manifest_entry(entry)
        if errors:
            raise ValueError(f"invalid manifest entry {entry.get('name')!r}: {errors}")
    return entries


def _run_one(base_url: str, entry: dict, timeout: int) -> tuple[str, str]:
    """Returns (status, detail) where status is PASS/FAIL/MANUAL."""
    trace_id = submit(base_url, entry["repo_url"], entry["concern"])
    status_resp = poll(base_url, trace_id, timeout=timeout)
    if status_resp["status"] != "done":
        return "FAIL", f"job reached status={status_resp['status']}"

    report = extract_report(status_resp)
    expectations = entry["assert"]
    if is_manual(expectations):
        findings = [f.get("dep_name") for f in report.get("findings") or []]
        return "MANUAL", f"findings={findings}"

    failures = evaluate(expectations, report)
    if failures:
        return "FAIL", "; ".join(failures)
    return "PASS", "ok"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the E2E fixture corpus")
    parser.add_argument("--manifest", default="tests/e2e/corpus.yaml")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--assert-live", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    live = args.assert_live or os.environ.get("CORPUS_PAT_AVAILABLE") == "1"
    entries = load_manifest(args.manifest)
    if args.only:
        entries = [e for e in entries if e["name"] == args.only]
        if not entries:
            print(f"FAIL: no manifest entry named '{args.only}'")
            sys.exit(1)

    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "MANUAL": 0}
    rows: list[tuple[str, str, str, str]] = []

    for entry in entries:
        name = entry["name"]
        if not live:
            rows.append((name, "-", "SKIP", f"({_REASON_NO_PAT})"))
            counts["SKIP"] += 1
            continue

        status, detail = _run_one(args.base_url, entry, args.timeout)
        mode = next(iter(entry["assert"]))
        rows.append((name, mode, status, detail))
        counts[status] += 1

    print(f"\n{'name':45s} {'mode':12s} {'status':8s} detail")
    for name, mode, status, detail in rows:
        print(f"{name:45s} {mode:12s} {status:8s} {detail}")

    print(
        f"\nPASS={counts['PASS']} FAIL={counts['FAIL']} "
        f"SKIP={counts['SKIP']} MANUAL={counts['MANUAL']}"
    )

    if counts["FAIL"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except URLError as exc:
        print(f"BLOCKED: cannot reach backend — {exc}")
        sys.exit(2)
