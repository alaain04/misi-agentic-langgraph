# Analysis Finding Version Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured `installed_version`, `fixed_version`, and `is_semver_major` fields to `FindingNote`, populated deterministically by `parse_trivy_vuln_findings` from Trivy's own vulnerability-scan output, so the version data Trivy already returns is available structurally instead of only as free text.

**Architecture:** Two small, additive changes: (1) three new optional fields on the existing `FindingNote` pydantic model, all defaulting to `None` so every existing caller (64 call sites, all keyword-based or `**dict`-unpacked) keeps working unchanged; (2) a self-contained semver-major comparison, computed inline in `parse_trivy_vuln_findings` from `InstalledVersion`/`FixedVersion` strings already present in Trivy's JSON — no new tool call, no external semver library, no LLM.

**Tech Stack:** Python 3.12, Pydantic, pytest. No new dependencies.

## Global Constraints

- All three new `FindingNote` fields default to `None` — no breaking change to any existing caller (spec D1).
- `is_semver_major` computation is deterministic and self-contained: parse `InstalledVersion`/`FixedVersion` as semver, compare major segments only. No new tool call, no npm CLI, no LLM (spec D2).
- `None` on a Trivy-sourced finding means "not computable" (no fix available, or either version string not parseable as semver) — never a silent false negative (spec D1).
- **Scope note (spec D2 addendum):** `is_semver_major` is structurally a same-dependency-upgrade comparison only, because Trivy's `InstalledVersion`/`FixedVersion` are always for the same `PkgName`. It has no meaning for a package replacement/migration. Nothing in this plan touches replacement/migration logic (that lives in the remediation subgraph, out of scope here) — this constraint is captured so the future follow-up that consumes these fields in `classify_targets_node` does not read `is_semver_major` as a signal once a target's resolution is `strategy="replace"`.
- Non-vulnerable-but-outdated packages (`npm outdated`'s domain) remain out of scope for `FindingNote` (spec D3).
- No other finding source (license, dependency graph) is touched. No backfill of historical records needed (defaults handle old records on read).

---

### Task 1: Add structured version fields to `FindingNote`

**Files:**
- Modify: `apps/backend/src/models/conductor.py:16-20`
- Test: `apps/backend/tests/unit/models/test_conductor_models.py`

**Interfaces:**
- Produces: `FindingNote.installed_version: str | None` (default `None`), `FindingNote.fixed_version: str | None` (default `None`), `FindingNote.is_semver_major: bool | None` (default `None`). Task 2 sets these three fields when constructing `FindingNote` in `parse_trivy_vuln_findings`.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/models/test_conductor_models.py`:

```python
def test_finding_note_version_fields_default_none():
    fn = FindingNote(dep_name="lodash", severity="high", description="desc", evidence=[])
    assert fn.installed_version is None
    assert fn.fixed_version is None
    assert fn.is_semver_major is None


def test_finding_note_version_fields_round_trip():
    fn = FindingNote(
        dep_name="lodash",
        severity="high",
        description="desc",
        evidence=[],
        installed_version="4.17.15",
        fixed_version="4.17.19",
        is_semver_major=False,
    )
    assert fn.installed_version == "4.17.15"
    assert fn.fixed_version == "4.17.19"
    assert fn.is_semver_major is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_conductor_models.py -v -k version_fields`
Expected: FAIL with a pydantic validation error (`installed_version`/`fixed_version`/`is_semver_major` are not valid fields for `FindingNote`).

- [ ] **Step 3: Add the three fields to `FindingNote`**

In `apps/backend/src/models/conductor.py`, change:

```python
class FindingNote(BaseModel):
    dep_name: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    description: str
    evidence: list[EvidenceRef]
```

to:

```python
class FindingNote(BaseModel):
    dep_name: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    description: str
    evidence: list[EvidenceRef]
    installed_version: str | None = None
    fixed_version: str | None = None
    # Same-dependency-upgrade comparison only (Trivy always reports Installed/
    # FixedVersion for the same PkgName) - never a signal for a package
    # replacement/migration. None means "not computable": no fix available,
    # or either version string isn't parseable as semver.
    is_semver_major: bool | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_conductor_models.py -v`
Expected: PASS (all tests in the file, including the two new ones and every pre-existing one — confirms no existing caller broke).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/models/conductor.py apps/backend/tests/unit/models/test_conductor_models.py
git commit -m "feat: add structured version fields to FindingNote"
```

---

### Task 2: Populate version fields in `parse_trivy_vuln_findings`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py`
- Test: `apps/backend/tests/unit/test_trivy_vuln_parser.py`

**Interfaces:**
- Consumes: `FindingNote.installed_version`, `FindingNote.fixed_version`, `FindingNote.is_semver_major` (from Task 1, all `str | None` / `bool | None`, default `None`).
- Produces: `parse_trivy_vuln_findings` now sets these three fields on every returned `FindingNote`, in addition to its existing behavior (unchanged: `dep_name`, `severity`, `description`, `evidence` construction, severity filtering, and most-severe-first sort).

- [ ] **Step 1: Write the failing tests**

Add to `apps/backend/tests/unit/test_trivy_vuln_parser.py`:

```python
def test_major_bump_sets_is_semver_major_true():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-MAJOR",
            "PkgName": "lodash",
            "InstalledVersion": "3.10.1",
            "FixedVersion": "4.17.19",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "3.10.1"
    assert findings[0].fixed_version == "4.17.19"
    assert findings[0].is_semver_major is True


def test_minor_patch_bump_sets_is_semver_major_false():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-MINOR",
            "PkgName": "lodash",
            "InstalledVersion": "4.17.15",
            "FixedVersion": "4.17.19",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "4.17.15"
    assert findings[0].fixed_version == "4.17.19"
    assert findings[0].is_semver_major is False


def test_no_fix_available_leaves_semver_fields_none():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-NOFIX",
            "PkgName": "left-pad",
            "InstalledVersion": "1.3.0",
            "FixedVersion": None,
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "1.3.0"
    assert findings[0].fixed_version is None
    assert findings[0].is_semver_major is None


def test_unparseable_version_leaves_is_semver_major_none():
    output = _trivy_output(
        {
            "VulnerabilityID": "CVE-WEIRD",
            "PkgName": "oddpkg",
            "InstalledVersion": "unstable",
            "FixedVersion": "2.0.0",
            "Severity": "HIGH",
            "Title": "t",
            "Description": "d",
            "PrimaryURL": "",
        }
    )
    findings = parse_trivy_vuln_findings(output, min_severity="high")
    assert findings[0].installed_version == "unstable"
    assert findings[0].fixed_version == "2.0.0"
    assert findings[0].is_semver_major is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_trivy_vuln_parser.py -v`
Expected: FAIL — `installed_version`/`fixed_version`/`is_semver_major` assertions fail (currently always `None` since `FindingNote` is constructed without them).

- [ ] **Step 3: Implement the semver-major computation and wire it in**

In `apps/backend/src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py`, add `re` to imports and add two module-level helpers plus wire their output into the `FindingNote` construction:

```python
from __future__ import annotations

import re

from src.models.conductor import EvidenceRef, FindingNote

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "UNKNOWN": "info",
}
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_SEMVER_RE = re.compile(r"^(\d+)\.\d+\.\d+(?:[-+].*)?$")


def _rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def _major_version(version: str) -> int | None:
    match = _SEMVER_RE.match(version.strip())
    return int(match.group(1)) if match else None


def _is_semver_major(installed: str | None, fixed: str | None) -> bool | None:
    """Same-dependency-upgrade comparison only - Trivy's InstalledVersion/
    FixedVersion are always for the same PkgName, so this has no meaning for
    a package replacement/migration. None means not computable: no fix
    available, or either version string isn't parseable as semver."""
    if not installed or not fixed:
        return None
    installed_major = _major_version(installed)
    fixed_major = _major_version(fixed)
    if installed_major is None or fixed_major is None:
        return None
    return installed_major != fixed_major
```

Then, inside `parse_trivy_vuln_findings`, change:

```python
            installed = vuln.get("InstalledVersion", "unknown")
            fixed = vuln.get("FixedVersion") or "no fix available"
            vuln_id = vuln.get("VulnerabilityID", "unknown")
            findings.append(
                FindingNote(
                    dep_name=vuln.get("PkgName", "unknown"),
                    severity=severity,
                    description=(
                        f"{vuln.get('Title') or vuln_id}. "
                        f"{vuln.get('Description', '')} "
                        f"Installed {installed}; fixed in {fixed}."
                    ),
                    evidence=[
                        EvidenceRef(
                            tool="trivy",
                            url=vuln.get("PrimaryURL") or None,
                            log_snippet=(
                                f"{vuln_id}: severity={vuln.get('Severity')}; "
                                f"installed={installed}; fixed={fixed}"
                            ),
                        )
                    ],
                )
            )
```

to:

```python
            installed_raw = vuln.get("InstalledVersion") or None
            fixed_raw = vuln.get("FixedVersion") or None
            installed = installed_raw or "unknown"
            fixed = fixed_raw or "no fix available"
            vuln_id = vuln.get("VulnerabilityID", "unknown")
            findings.append(
                FindingNote(
                    dep_name=vuln.get("PkgName", "unknown"),
                    severity=severity,
                    description=(
                        f"{vuln.get('Title') or vuln_id}. "
                        f"{vuln.get('Description', '')} "
                        f"Installed {installed}; fixed in {fixed}."
                    ),
                    evidence=[
                        EvidenceRef(
                            tool="trivy",
                            url=vuln.get("PrimaryURL") or None,
                            log_snippet=(
                                f"{vuln_id}: severity={vuln.get('Severity')}; "
                                f"installed={installed}; fixed={fixed}"
                            ),
                        )
                    ],
                    installed_version=installed_raw,
                    fixed_version=fixed_raw,
                    is_semver_major=_is_semver_major(installed_raw, fixed_raw),
                )
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_trivy_vuln_parser.py -v`
Expected: PASS (all tests in the file, including the four new ones and every pre-existing one — confirms `description`/`evidence` text and existing severity-filter/sort behavior are unchanged).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/trivy_vuln_parser.py apps/backend/tests/unit/test_trivy_vuln_parser.py
git commit -m "feat: compute structured version fields in parse_trivy_vuln_findings"
```

---

### Task 3: Full verification

**Files:** None (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `cd apps/backend && uv run pytest`
Expected: PASS, zero failures (confirms the 64 existing `FindingNote` call sites across the codebase are unaffected).

- [ ] **Step 2: Run lint and type checks**

Run: `cd apps/backend && uv run ruff check . && uv run mypy src`
Expected: Both clean, zero errors.

- [ ] **Step 3: Commit if either step required fixes**

Only if Steps 1-2 required code changes:

```bash
git add -A
git commit -m "fix: address lint/type/test issues from version enrichment"
```

If nothing needed fixing, no commit — Tasks 1-2 already captured the working state.

---

## Self-Review Notes

- **Spec coverage:** D1 (three fields, all default `None`) → Task 1. D2 (deterministic `is_semver_major` from Trivy's own strings, no new tool/LLM) → Task 2, including the D2 scope-note addendum captured as a code comment on `_is_semver_major` and in this plan's Global Constraints. D3 (non-vulnerable-but-outdated stays out of scope) → nothing added for it, by design. Success criteria's three required test scenarios (major bump, minor/patch bump, no-fix-available) → Task 2 Step 1, plus one extra (unparseable version) for D1's "not parseable as semver" None-reason.
- **No placeholders:** every step has literal code/commands, no "TBD" or "similar to Task N".
- **Type consistency:** `installed_version: str | None`, `fixed_version: str | None`, `is_semver_major: bool | None` are the exact names/types used identically in Task 1 (model) and Task 2 (parser construction call).
