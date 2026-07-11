# Pipeline Quality Improvements & E2E Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three output quality issues (A→B→C) in the analysis pipeline and validate with an expanded 3-run E2E test suite covering distinct Node.js repos and concerns.

**Architecture:** Three sequential fix tasks, each with unit tests, followed by a reusable E2E validation script and execution of the test suite. Fixes are applied in order A→B→C; each is independently committed and testable before moving on.

**Tech Stack:** Python 3.12, LangGraph, FastAPI, MongoDB, pytest, uv

## Global Constraints
- Run tests with `uv run pytest <path>`, never `python`
- Backend runs on `http://localhost:8001` (started via `make dev`)
- MongoDB must be running on `localhost:27017`
- No new dependencies added
- Do not modify `MainState` TypedDict (no new state keys)
- `asyncio_mode = "auto"` is set in `pyproject.toml` — async test functions work without decorators

---

## File Map

| File | Change |
|------|--------|
| `src/main_graph/nodes/report_builder.py` | Add `overall_risk_level` + `recommendations` to report |
| `tests/unit/nodes/test_report_builder.py` | New tests for both new fields |
| `src/main_graph/utils/confidence.py` | Add `_CONFIDENCE_THRESHOLD = 0.25` to `compute_severity` |
| `tests/unit/utils/test_confidence.py` | New tests for threshold behavior |
| `src/services/job_runner.py` | Add `EVIDENCE_COLLECTOR` import; add start/complete artifact handlers for collector + correlator |
| `scripts/e2e_check.py` | New script — drives one full E2E cycle and checks success criteria |

---

### Task 1: Fix A — report_builder output quality

**Files:**
- Modify: `src/main_graph/nodes/report_builder.py`
- Modify: `tests/unit/nodes/test_report_builder.py`

**Interfaces:**
- Consumes: `state["risk_findings"]` — `list[RiskFinding]` where each has `.severity: str`, `.risk_score: float`, `.recommendation: str | None`
- Produces: `state["analysis_report"]` — dict now includes `"overall_risk_level": str` (one of `"none" | "info" | "low" | "medium" | "high" | "critical"`) and `"recommendations": list[str]` (deduplicated, ordered by descending `risk_score`)

- [ ] **Step 1: Write failing tests for `overall_risk_level` and `recommendations`**

Add these tests at the bottom of `tests/unit/nodes/test_report_builder.py`:

```python
def test_report_builder_overall_risk_level_picks_max_severity():
    state = {
        "concern": "security",
        "risk_findings": [
            _make_finding("lodash", 8.5, "high"),
            _make_finding("express", 3.0, "low"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    assert result["analysis_report"]["overall_risk_level"] == "high"


def test_report_builder_overall_risk_level_none_when_no_findings():
    state = {"concern": "test", "risk_findings": [], "contradictions": []}
    result = report_builder(state)
    assert result["analysis_report"]["overall_risk_level"] == "none"


def test_report_builder_recommendations_deduplicated():
    # both findings have recommendation="update" — should appear once
    state = {
        "concern": "security",
        "risk_findings": [
            _make_finding("lodash", 8.5, "high"),
            _make_finding("express", 3.0, "low"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    assert result["analysis_report"]["recommendations"] == ["update"]


def test_report_builder_recommendations_empty_when_all_null():
    from src.models.risk_finding import RiskFinding
    finding = RiskFinding(
        dep_name="dep", risk_score=1.0, confidence=0.5, severity="low",
        hypotheses=[], supporting_evidence=[], contradictions=[], missing_evidence=[],
        summary="s", recommendation=None, alternatives=[],
    )
    state = {"concern": "t", "risk_findings": [finding], "contradictions": []}
    result = report_builder(state)
    assert result["analysis_report"]["recommendations"] == []


def test_report_builder_recommendations_ordered_by_risk_score():
    from src.models.risk_finding import RiskFinding

    def _make_rec(dep, score, severity, rec):
        return RiskFinding(
            dep_name=dep, risk_score=score, confidence=0.8, severity=severity,
            hypotheses=[], supporting_evidence=[], contradictions=[], missing_evidence=[],
            summary=f"{dep} summary", recommendation=rec, alternatives=[],
        )

    state = {
        "concern": "security",
        "risk_findings": [
            _make_rec("z_dep", 2.0, "low", "pin z_dep"),
            _make_rec("a_dep", 9.0, "high", "update a_dep immediately"),
            _make_rec("m_dep", 5.0, "medium", "monitor m_dep"),
        ],
        "contradictions": [],
    }
    result = report_builder(state)
    recs = result["analysis_report"]["recommendations"]
    assert recs[0] == "update a_dep immediately"
    assert recs[1] == "monitor m_dep"
    assert recs[2] == "pin z_dep"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/nodes/test_report_builder.py -v -k "overall_risk_level or recommendations"
```
Expected: FAILED (KeyError — fields don't exist yet)

- [ ] **Step 3: Replace `src/main_graph/nodes/report_builder.py` with the updated implementation**

```python
"""Deterministic assembly of analysis report from risk findings and contradictions."""
from __future__ import annotations

from datetime import UTC, datetime

from src.main_graph.state import MainState
from src.models.risk_finding import RiskFinding

_SEVERITY_ORDER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _overall_risk_level(findings: list[RiskFinding]) -> str:
    if not findings:
        return "none"
    return max(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 0)).severity


def _aggregate_recommendations(sorted_findings: list[RiskFinding]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for f in sorted_findings:
        if f.recommendation and f.recommendation not in seen:
            seen.add(f.recommendation)
            result.append(f.recommendation)
    return result


def _finding_to_dict(f: RiskFinding) -> dict:
    return {
        "dep_name": f.dep_name,
        "risk_score": f.risk_score,
        "confidence": f.confidence,
        "severity": f.severity,
        "summary": f.summary,
        "recommendation": f.recommendation,
        "alternatives": f.alternatives,
        "supporting_evidence_count": len(f.supporting_evidence),
        "contradictions_count": len(f.contradictions),
        "missing_evidence": f.missing_evidence,
    }


def report_builder(state: MainState) -> dict:
    findings = state.get("risk_findings") or []
    contradictions = state.get("contradictions") or []

    sorted_findings = sorted(findings, key=lambda f: f.risk_score, reverse=True)

    report = {
        "concern": state.get("concern", ""),
        "generated_at": datetime.now(UTC).isoformat(),
        "overall_risk_level": _overall_risk_level(findings),
        "summary": {
            "total_deps": len(findings),
            "critical": sum(1 for f in findings if f.severity == "critical"),
            "high": sum(1 for f in findings if f.severity == "high"),
            "medium": sum(1 for f in findings if f.severity == "medium"),
            "low": sum(1 for f in findings if f.severity == "low"),
        },
        "findings": [_finding_to_dict(f) for f in sorted_findings],
        "recommendations": _aggregate_recommendations(sorted_findings),
        "contradictions": [
            {"description": c.description, "resolution": c.resolution}
            for c in contradictions
        ],
    }

    return {"analysis_report": report}
```

- [ ] **Step 4: Run the full report_builder test suite**

```bash
uv run pytest tests/unit/nodes/test_report_builder.py -v
```
Expected: all PASS (existing tests + 5 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/report_builder.py tests/unit/nodes/test_report_builder.py
git commit -m "feat: add overall_risk_level and recommendations to analysis report"
```

---

### Task 2: Fix B — severity/confidence threshold

**Files:**
- Modify: `src/main_graph/utils/confidence.py`
- Modify: `tests/unit/utils/test_confidence.py`

**Interfaces:**
- `compute_severity(evidence: list[Evidence]) -> Severity` — same signature; now ignores supporting evidence with `confidence < 0.25`; returns `"low"` if nothing passes the threshold
- `compute_confidence` and `compute_risk_score` are unchanged

- [ ] **Step 1: Write failing tests for the threshold behavior**

Add to `tests/unit/utils/test_confidence.py`:

```python
def test_compute_severity_suppressed_below_threshold():
    # medium-severity evidence but confidence=0.1 → below threshold → "low"
    evs = [
        _make_evidence("maintainer_signal", "dep", "MaintainerTrustSkill",
                       confidence=0.1, reliability=0.8, supports=True, severity="medium"),
    ]
    assert compute_severity(evs) == "low"


def test_compute_severity_kept_at_threshold():
    # confidence exactly at threshold (0.25) → still counts
    evs = [
        _make_evidence("maintainer_signal", "dep", "MaintainerTrustSkill",
                       confidence=0.25, reliability=0.8, supports=True, severity="medium"),
    ]
    assert compute_severity(evs) == "medium"


def test_compute_severity_low_confidence_critical_masked_by_high_confidence_medium():
    # critical at 5% confidence + medium at 60% confidence → medium wins
    evs = [
        _make_evidence("vulnerability", "dep", "VulnerabilitySkill",
                       confidence=0.05, reliability=0.9, supports=True, severity="critical"),
        _make_evidence("ecosystem", "dep", "EcosystemSkill",
                       confidence=0.6, reliability=0.8, supports=True, severity="medium"),
    ]
    assert compute_severity(evs) == "medium"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/utils/test_confidence.py -v -k "suppressed or threshold or masked"
```
Expected: FAILED (current `compute_severity` ignores confidence)

- [ ] **Step 3: Update `compute_severity` in `src/main_graph/utils/confidence.py`**

Replace only the `compute_severity` function (add `_CONFIDENCE_THRESHOLD` constant above it):

```python
_CONFIDENCE_THRESHOLD = 0.25


def compute_severity(evidence: list[Evidence]) -> Severity:
    supporting = [
        e for e in evidence
        if e.supports_hypothesis and e.severity and e.confidence >= _CONFIDENCE_THRESHOLD
    ]
    if not supporting:
        return "low"
    best = max(supporting, key=lambda e: _SEVERITY_ORDER.get(e.severity or "info", 0))
    return best.severity or "low"
```

Full file after the change:

```python
from __future__ import annotations

from src.models.evidence import Evidence, Severity
from src.models.risk_finding import ContradictionReport

_SEVERITY_ORDER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_SEVERITY_BASE: dict[str, float] = {"critical": 10.0, "high": 7.5, "medium": 5.0, "low": 2.5}
_CONFIDENCE_THRESHOLD = 0.25


def compute_confidence(
    evidence: list[Evidence],
    contradictions: list[ContradictionReport],
    dep_name: str,
) -> float:
    dep_evs = [e for e in evidence if e.dep_name == dep_name]
    if not dep_evs:
        return 0.0

    base = sum(e.confidence * e.reliability for e in dep_evs) / len(dep_evs)

    dep_ev_ids = {e.id for e in dep_evs}
    unresolved = sum(
        1 for c in contradictions
        if c.resolution == "unresolved" and any(eid in dep_ev_ids for eid in c.evidence_ids)
    )
    penalty = 0.2 * unresolved

    supporting_skills = {e.skill_id for e in dep_evs if e.supports_hypothesis}
    bonus = 0.1 if len(supporting_skills) >= 2 else 0.0

    return max(0.0, min(1.0, base - penalty + bonus))


def compute_severity(evidence: list[Evidence]) -> Severity:
    supporting = [
        e for e in evidence
        if e.supports_hypothesis and e.severity and e.confidence >= _CONFIDENCE_THRESHOLD
    ]
    if not supporting:
        return "low"
    best = max(supporting, key=lambda e: _SEVERITY_ORDER.get(e.severity or "info", 0))
    return best.severity or "low"


def compute_risk_score(severity: Severity, confidence: float) -> float:
    return round(_SEVERITY_BASE.get(severity, 2.5) * confidence, 1)
```

- [ ] **Step 4: Run full confidence test suite**

```bash
uv run pytest tests/unit/utils/test_confidence.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/utils/confidence.py tests/unit/utils/test_confidence.py
git commit -m "fix: apply confidence threshold in compute_severity to prevent label/score mismatch"
```

---

### Task 3: Fix C — pipeline observability

**Files:**
- Modify: `src/services/job_runner.py`

**Interfaces:**
- `evidence_collector` and `evidence_correlator` artifacts now appear in `job.artifacts` with start/complete lifecycle
- For `evidence_correlator`: artifact `data` field contains `findings_count`, `contradictions_count`, `deps_covered`
- Node functions themselves are not modified

- [ ] **Step 1: Add `EVIDENCE_COLLECTOR` to the import in `job_runner.py`**

Find:
```python
from src.main_graph.constants import (
    EVIDENCE_CORRELATOR,
    FINDING_REVIEWER,
    INVESTIGATION_PLANNER,
    REPORT_BUILDER,
)
```

Replace with:
```python
from src.main_graph.constants import (
    EVIDENCE_COLLECTOR,
    EVIDENCE_CORRELATOR,
    FINDING_REVIEWER,
    INVESTIGATION_PLANNER,
    REPORT_BUILDER,
)
```

- [ ] **Step 2: Replace the `evidence_correlator` handler and add `evidence_collector` handler in `_stream_graph`**

Find the existing block:
```python
            elif node_name == EVIDENCE_CORRELATOR:
                if "risk_findings" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        EVIDENCE_CORRELATOR,
                        {"output": {"finding_count": len(node_update["risk_findings"])}},
                    )
```

Replace with:
```python
            elif node_name == EVIDENCE_COLLECTOR:
                await dao.start_artifact(job_id, EVIDENCE_COLLECTOR)
                await dao.complete_artifact(job_id, EVIDENCE_COLLECTOR, "done")
            elif node_name == EVIDENCE_CORRELATOR:
                await dao.start_artifact(job_id, EVIDENCE_CORRELATOR)
                findings = node_update.get("risk_findings") or []
                contradictions = node_update.get("contradictions") or []
                await dao.update_artifact_data(job_id, EVIDENCE_CORRELATOR, {
                    "data": {
                        "findings_count": len(findings),
                        "contradictions_count": len(contradictions),
                        "deps_covered": [f.dep_name for f in findings],
                    }
                })
                await dao.complete_artifact(job_id, EVIDENCE_CORRELATOR, "done")
```

- [ ] **Step 3: Verify the full if/elif chain in `_stream_graph` is correct**

After the edit, the complete handler block should read:

```python
        for node_name, node_update in chunk.items():
            if node_name == "__interrupt__":
                interrupt_payload = node_update[0].value
                continue

            if node_name == "discovery":
                if node_update.get("discovery_error") or node_update.get("sbom_error"):
                    await dao.complete_artifact(job_id, "discovery", "failed")
                else:
                    await dao.complete_artifact(job_id, "discovery", "done")
                    await dao.start_artifact(job_id, INVESTIGATION_PLANNER)
            elif node_name == INVESTIGATION_PLANNER:
                await dao.complete_artifact(job_id, INVESTIGATION_PLANNER, "done")
            elif node_name == EVIDENCE_COLLECTOR:
                await dao.start_artifact(job_id, EVIDENCE_COLLECTOR)
                await dao.complete_artifact(job_id, EVIDENCE_COLLECTOR, "done")
            elif node_name == EVIDENCE_CORRELATOR:
                await dao.start_artifact(job_id, EVIDENCE_CORRELATOR)
                findings = node_update.get("risk_findings") or []
                contradictions = node_update.get("contradictions") or []
                await dao.update_artifact_data(job_id, EVIDENCE_CORRELATOR, {
                    "data": {
                        "findings_count": len(findings),
                        "contradictions_count": len(contradictions),
                        "deps_covered": [f.dep_name for f in findings],
                    }
                })
                await dao.complete_artifact(job_id, EVIDENCE_CORRELATOR, "done")
            elif node_name == FINDING_REVIEWER:
                await dao.start_artifact(job_id, FINDING_REVIEWER)
                if "review_approved" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        FINDING_REVIEWER,
                        {
                            "output": {
                                "review_approved": node_update.get("review_approved"),
                                "reviewer_feedback": node_update.get("reviewer_feedback"),
                            }
                        },
                    )
                await dao.complete_artifact(job_id, FINDING_REVIEWER, "done")
            elif node_name == REPORT_BUILDER:
                await dao.start_artifact(job_id, REPORT_BUILDER)
                if "analysis_report" in node_update:
                    await dao.update_artifact_data(
                        job_id,
                        REPORT_BUILDER,
                        {"output": node_update["analysis_report"]},
                    )
                await dao.complete_artifact(job_id, REPORT_BUILDER, "done")
```

- [ ] **Step 4: Run unit tests to confirm nothing regressed**

```bash
uv run pytest tests/unit/ -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/job_runner.py
git commit -m "feat: add artifact lifecycle tracking for evidence_collector and evidence_correlator"
```

---

### Task 4: E2E test runner script

**Files:**
- Create: `scripts/e2e_check.py`

**Interfaces:**
- CLI: `uv run python scripts/e2e_check.py --repo <url> --concern <text> [--base-url http://localhost:8001] [--timeout 600]`
- Exit 0 = all criteria passed; Exit 1 = failure or criteria miss; Exit 2 = backend unreachable

- [ ] **Step 1: Create `scripts/e2e_check.py`**

```python
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
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/e2e_check.py
```

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e_check.py
git commit -m "chore: add e2e_check.py script for automated pipeline validation"
```

---

### Task 5: Run E2E test suite (3 runs)

**Prerequisite:** Tasks 1–4 committed. Backend restarted to load code changes. MongoDB running.

- [ ] **Step 1: Restart the backend**

Stop the current server (Ctrl+C in the terminal running `make dev`) and restart:
```bash
make dev
```
Wait for `Application startup complete.` in the logs.

- [ ] **Step 2: Run 2 — expressjs/express, vulnerability concern**

```bash
uv run python scripts/e2e_check.py \
  --repo "https://github.com/expressjs/express" \
  --concern "Are there vulnerable or outdated production dependencies in this Express.js project?" \
  --timeout 600
```

Expected:
- Plan targets express, accepts, depd, ms, debug, finalhandler (production dependencies)
- `overall_risk_level` set (non-empty)
- At least one finding with `risk_score > 2.0` given the age of some express transitive deps
- `recommendations` non-empty
- `evidence_collector` and `evidence_correlator` in artifacts list
- Exit 0

- [ ] **Step 3: Run 3 — tc39-proposal-iterator-helpers, license concern**

```bash
uv run python scripts/e2e_check.py \
  --repo "https://github.com/nicolo-ribaudo/tc39-proposal-iterator-helpers" \
  --concern "Are any dependencies using GPL or other restrictive licenses that could affect open-source distribution?" \
  --timeout 600
```

Expected:
- Plan assigns `LicenseSkill` to identified dependencies
- Findings are low/info (minimal deps, likely MIT/Apache licensed)
- `overall_risk_level` = `"none"`, `"info"`, or `"low"`
- No severity/score contradiction (Fix B validates this)
- Exit 0

- [ ] **Step 4: Run 4 — Rich-Harris/degit, maintainer health concern**

```bash
uv run python scripts/e2e_check.py \
  --repo "https://github.com/Rich-Harris/degit" \
  --concern "Are any dependencies in this project abandoned or poorly maintained?" \
  --timeout 600
```

Expected:
- Plan assigns `MaintainerTrustSkill` to key dependencies
- Some findings with maintainer/ecosystem signals
- All criteria pass
- Exit 0

- [ ] **Step 5: For any FAIL — identify and fix before continuing**

Read the failure line printed by the script. Common patterns:

| Failure message | Root cause | Fix location |
|----------------|-----------|-------------|
| `overall_risk_level missing` | `_overall_risk_level` not reached | `report_builder.py` — check `findings` is populated |
| `recommendations empty but N findings have risk_score > 2.0` | All `finding.recommendation` are `None` | LLM synthesis in `evidence_correlator.py` — check `_SYNTHESIS_SYSTEM` prompt |
| `severity=X but risk_score=Y` | `_CONFIDENCE_THRESHOLD` not filtering | `confidence.py` — verify threshold applied |
| `evidence_collector artifact missing` | `job_runner.py` not updated | Re-check Task 3 edit |

After any fix, re-run only the failed test case before moving on.

- [ ] **Step 6: Final full-suite pass**

Run all three in sequence and confirm all exit 0:

```bash
uv run python scripts/e2e_check.py \
  --repo "https://github.com/expressjs/express" \
  --concern "Are there vulnerable or outdated production dependencies in this Express.js project?" \
  --timeout 600

uv run python scripts/e2e_check.py \
  --repo "https://github.com/nicolo-ribaudo/tc39-proposal-iterator-helpers" \
  --concern "Are any dependencies using GPL or other restrictive licenses that could affect open-source distribution?" \
  --timeout 600

uv run python scripts/e2e_check.py \
  --repo "https://github.com/Rich-Harris/degit" \
  --concern "Are any dependencies in this project abandoned or poorly maintained?" \
  --timeout 600
```

Expected: all three print `PASS: all success criteria met`
