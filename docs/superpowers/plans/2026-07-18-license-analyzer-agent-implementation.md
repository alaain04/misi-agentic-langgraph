# License Analyzer Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Context

`docs/superpowers/specs/2026-07-18-license-analyzer-agent-design.md` is an approved design for a new `LicenseAgent` that detects license-compatibility risk across a project's dependency tree (rights conflicts, obligation gaps, copyleft contagion), replacing the existing `check_licenses` tool, which only works when `node_modules` happens to be on disk (the uncommon case — see spec's "Why `check_licenses` doesn't work today"). This plan turns that spec into buildable, TDD tasks.

I read every file the spec touches or references (`base_agent.py`, `vulnerability_agent.py` as the deterministic-agent pattern to follow, `registry.py`, `analysis_conductor.py`, `audit_parser.py` as the `FindingNote`-shaping precedent, `external_api.py`'s `_npm_metadata`, `package_files.py`'s `check_licenses` and `_load_pkg`, `dependency_graph.py`'s lock parsers, `models/conductor.py` and `models/results.py`, and the existing test files for each) so every signature and file path below is verified against the current codebase, not guessed.

**Key implementation decisions not fully spelled out in the spec** (all within the spec's stated "approximate/simplify" scope):
- `resolve()` for `"A OR B"`: return whichever side is in the curated table first (left, then right); `None` if neither resolves.
- `resolve()` for `"A AND B"`: both sides must resolve; otherwise merge them into a synthetic entry that takes the *more restrictive* attitude per field (either license forbidding/requiring something means the combination does too). Any parenthesis in the raw string is treated as a nested expression and short-circuits to unknown, per spec.
- C3 "compatible category" check: since a full SPDX compatibility matrix is out of scope, treat the project's own license as compatible only if it's the exact same id as the dependency's, or if the project's own category is itself `strong_copyleft`/`network_copyleft` (i.e., the project already propagates rights).

**Global Constraints**
- No LLM calls in this agent — `LicenseAgent.run()` is fully deterministic, mirroring `VulnerabilityAgent` (spec: "legal-risk findings should not depend on an LLM's compatibility judgment").
- `packages_to_focus` is always ignored; `EvidenceBundle.packages_to_focus` is always returned as `[]`.
- Never guess a license or a conflict — anything outside the curated table becomes an `info`-severity "manual review" finding.
- Test files live flat under `apps/backend/tests/unit/` (matches `test_audit_parser.py`, `test_base_agent.py` — no per-agent subfolder in this codebase).
- Run tests with `uv run pytest <path> -v` from `apps/backend/`.

---

## Task 1: Curated license knowledge base (`license_data.py`)

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/agents/license_data.py`
- Test: `apps/backend/tests/unit/test_license_data.py`

**Interfaces:**
- Produces: `LicenseEntry` (frozen dataclass: `category`, `sublicense`, `commercial_use`, `include_notice`, `disclose_source`, `state_changes`, `same_license`), `LICENSES: dict[str, LicenseEntry]`, `resolve(expression: str | None) -> tuple[str, LicenseEntry] | None`. Consumed by Task 2 (`license_rules.py`) and Task 4 (`license_agent.py`).

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/tests/unit/test_license_data.py
from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.license_data import LICENSES, resolve


def test_resolve_exact_id_returns_curated_entry():
    resolved = resolve("MIT")
    assert resolved == ("MIT", LICENSES["MIT"])


def test_resolve_unknown_id_returns_none():
    assert resolve("WTFPL") is None


def test_resolve_empty_or_none_returns_none():
    assert resolve("") is None
    assert resolve(None) is None


def test_resolve_see_license_in_file_returns_none():
    assert resolve("SEE LICENSE IN LICENSE.txt") is None


def test_resolve_or_picks_first_known_side():
    assert resolve("MIT OR Apache-2.0") == ("MIT", LICENSES["MIT"])


def test_resolve_or_falls_back_to_second_side():
    assert resolve("Foo-Bar OR MIT") == ("MIT", LICENSES["MIT"])


def test_resolve_or_unknown_when_neither_side_known():
    assert resolve("Foo OR Bar") is None


def test_resolve_and_combines_both_sides_most_restrictive():
    resolved = resolve("MIT AND Apache-2.0")
    assert resolved is not None
    key, entry = resolved
    assert key == "MIT AND Apache-2.0"
    assert entry.category == "permissive"
    assert entry.sublicense == "can"
    assert entry.state_changes == "must"  # Apache-2.0's must wins over MIT's not_required


def test_resolve_and_unknown_if_either_side_unknown():
    assert resolve("MIT AND Foo") is None


def test_resolve_rejects_nested_parenthesized_expression():
    assert resolve("(MIT OR Apache-2.0) AND GPL-3.0-only") is None


def test_gpl_3_0_only_is_strong_copyleft_with_same_license_must():
    entry = LICENSES["GPL-3.0-only"]
    assert entry.category == "strong_copyleft"
    assert entry.same_license == "must"


def test_agpl_3_0_only_is_network_copyleft():
    assert LICENSES["AGPL-3.0-only"].category == "network_copyleft"


def test_unlicensed_sentinel_is_proprietary_and_grants_nothing():
    entry = LICENSES["UNLICENSED"]
    assert entry.category == "proprietary"
    assert entry.sublicense == "cannot"
    assert entry.commercial_use == "cannot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.analysis.agents.license_data'`

- [ ] **Step 3: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/agents/license_data.py
"""Curated SPDX license knowledge base for the license conflict rule engine.

Approximates the term model in Liu et al., "Catch the Butterfly: Peeking
into the Terms and Conflicts among SPDX Licenses" (arXiv:2401.10636) for the
SPDX ids common in the npm ecosystem, rather than the paper's full 453-license
NLP extraction. Anything outside this table resolves to `unknown` in
`resolve()` rather than being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Attitude = Literal["can", "cannot", "must", "not_required"]
Category = Literal[
    "public_domain",
    "permissive",
    "weak_copyleft",
    "strong_copyleft",
    "network_copyleft",
    "proprietary",
]


@dataclass(frozen=True)
class LicenseEntry:
    category: Category
    sublicense: Attitude
    commercial_use: Attitude
    include_notice: Attitude
    disclose_source: Attitude
    state_changes: Attitude
    same_license: Attitude


# Sentinel used when package.json has no "license" field or declares
# "UNLICENSED" — treated as proprietary/all-rights-reserved, the most
# restrictive stance (spec: this legitimately surfaces C1/C2 findings
# against most dependencies requiring attribution).
UNLICENSED_ID = "UNLICENSED"

LICENSES: dict[str, LicenseEntry] = {
    "MIT": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "ISC": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "Apache-2.0": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="must",
        same_license="not_required",
    ),
    "BSD-2-Clause": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "BSD-3-Clause": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "0BSD": LicenseEntry(
        category="permissive",
        sublicense="can",
        commercial_use="can",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "Unlicense": LicenseEntry(
        category="public_domain",
        sublicense="can",
        commercial_use="can",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "CC0-1.0": LicenseEntry(
        category="public_domain",
        sublicense="can",
        commercial_use="can",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
    "MPL-2.0": LicenseEntry(
        category="weak_copyleft",
        sublicense="can",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-2.1-only": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-2.1-or-later": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-3.0-only": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "LGPL-3.0-or-later": LicenseEntry(
        category="weak_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="not_required",
    ),
    "GPL-2.0-only": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "GPL-2.0-or-later": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "GPL-3.0-only": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "GPL-3.0-or-later": LicenseEntry(
        category="strong_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "AGPL-3.0-only": LicenseEntry(
        category="network_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    "AGPL-3.0-or-later": LicenseEntry(
        category="network_copyleft",
        sublicense="cannot",
        commercial_use="can",
        include_notice="must",
        disclose_source="must",
        state_changes="must",
        same_license="must",
    ),
    UNLICENSED_ID: LicenseEntry(
        category="proprietary",
        sublicense="cannot",
        commercial_use="cannot",
        include_notice="not_required",
        disclose_source="not_required",
        state_changes="not_required",
        same_license="not_required",
    ),
}

_CATEGORY_RANK: dict[Category, int] = {
    "public_domain": 0,
    "permissive": 1,
    "weak_copyleft": 2,
    "strong_copyleft": 3,
    "network_copyleft": 4,
    "proprietary": 5,
}
_CAN_FIELDS = ("sublicense", "commercial_use")
_MUST_FIELDS = ("include_notice", "disclose_source", "state_changes", "same_license")


def _combine_and(a: LicenseEntry, b: LicenseEntry) -> LicenseEntry:
    """Merge two licenses under an SPDX "AND" expression: the recipient must
    satisfy both simultaneously, so any restriction or obligation on either
    side applies to the combination."""
    category = a.category if _CATEGORY_RANK[a.category] >= _CATEGORY_RANK[b.category] else b.category
    kwargs: dict[str, str] = {"category": category}
    for field in _CAN_FIELDS:
        kwargs[field] = (
            "cannot" if "cannot" in (getattr(a, field), getattr(b, field)) else "can"
        )
    for field in _MUST_FIELDS:
        kwargs[field] = (
            "must" if "must" in (getattr(a, field), getattr(b, field)) else "not_required"
        )
    return LicenseEntry(**kwargs)  # type: ignore[arg-type]


def resolve(expression: str | None) -> tuple[str, LicenseEntry] | None:
    """Normalize a raw SPDX license expression to a curated (id, entry) pair.

    Supports exact ids and single-level "A OR B" / "A AND B" expressions.
    Anything else — custom text, `SEE LICENSE IN <file>`, nested/parenthesized
    expressions, or an id outside the curated table — returns None. The
    caller must record this as a manual-review finding, never guess.
    """
    expr = (expression or "").strip()
    if not expr or "(" in expr or ")" in expr:
        return None
    if expr in LICENSES:
        return expr, LICENSES[expr]
    if " OR " in expr:
        left, right = (side.strip() for side in expr.split(" OR ", 1))
        for side in (left, right):
            if side in LICENSES:
                return side, LICENSES[side]
        return None
    if " AND " in expr:
        left, right = (side.strip() for side in expr.split(" AND ", 1))
        if left in LICENSES and right in LICENSES:
            return f"{left} AND {right}", _combine_and(LICENSES[left], LICENSES[right])
        return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_data.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/license_data.py apps/backend/tests/unit/test_license_data.py
git commit -m "feat: add curated SPDX license knowledge base"
```

---

## Task 2: Conflict rule engine (`license_rules.py`)

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/agents/license_rules.py`
- Test: `apps/backend/tests/unit/test_license_rules.py`

**Interfaces:**
- Consumes: `LicenseEntry`, `LICENSES` from Task 1 (`license_data.py`).
- Produces: `Conflict` (frozen dataclass: `rule: str`, `severity: str`, `detail: str`), `check_conflicts(project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry) -> list[Conflict]`. Consumed by Task 4 (`license_agent.py`).

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/tests/unit/test_license_rules.py
from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.license_data import LICENSES
from src.main_graph.subgraphs.analysis.agents.license_rules import (
    _check_c1,
    _check_c2,
    _check_c3,
    check_conflicts,
)


def test_c1_no_conflict_when_both_can_sublicense():
    # spec example: project CAN sublicense + Apache-2.0 dependency (CAN sublicense)
    conflicts = _check_c1("MIT", LICENSES["MIT"], "Apache-2.0", LICENSES["Apache-2.0"])
    assert conflicts == []


def test_c1_flags_when_project_can_dependency_cannot():
    conflicts = _check_c1("MIT", LICENSES["MIT"], "GPL-3.0-only", LICENSES["GPL-3.0-only"])
    assert len(conflicts) == 1
    assert conflicts[0].rule == "C1"
    assert conflicts[0].severity == "medium"
    assert "MIT" in conflicts[0].detail
    assert "GPL-3.0-only" in conflicts[0].detail


def test_c2_flags_when_dependency_musts_obligation_project_lacks():
    # spec example: MIT dependency (requires include_notice) + no project license
    conflicts = _check_c2(
        "UNLICENSED", LICENSES["UNLICENSED"], "MIT", LICENSES["MIT"]
    )
    assert any(c.rule == "C2" and c.severity == "low" for c in conflicts)
    include_notice_conflicts = [c for c in conflicts if "notice" in c.detail]
    assert len(include_notice_conflicts) == 1


def test_c2_no_conflict_when_project_already_musts_same_obligation():
    conflicts = _check_c2("MIT", LICENSES["MIT"], "MIT", LICENSES["MIT"])
    assert conflicts == []


def test_c3_flags_copyleft_contagion_gpl_dependency_into_mit_project():
    # spec example: GPL-3.0-only dependency + MIT project -> C3/high
    conflict = _check_c3("MIT", LICENSES["MIT"], "GPL-3.0-only", LICENSES["GPL-3.0-only"])
    assert conflict is not None
    assert conflict.rule == "C3"
    assert conflict.severity == "high"


def test_c3_no_conflict_when_project_is_same_id():
    conflict = _check_c3(
        "GPL-3.0-only", LICENSES["GPL-3.0-only"], "GPL-3.0-only", LICENSES["GPL-3.0-only"]
    )
    assert conflict is None


def test_c3_no_conflict_when_project_itself_copyleft():
    conflict = _check_c3(
        "GPL-3.0-only", LICENSES["GPL-3.0-only"], "AGPL-3.0-only", LICENSES["AGPL-3.0-only"]
    )
    assert conflict is None


def test_c3_no_conflict_when_dependency_not_copyleft():
    conflict = _check_c3("MIT", LICENSES["MIT"], "Apache-2.0", LICENSES["Apache-2.0"])
    assert conflict is None


def test_check_conflicts_returns_all_three_rule_types_for_mit_project_gpl_dependency():
    conflicts = check_conflicts(
        "MIT", LICENSES["MIT"], "GPL-3.0-only", LICENSES["GPL-3.0-only"]
    )
    rules = {c.rule for c in conflicts}
    assert rules == {"C1", "C2", "C3"}
    assert max(c.severity for c in conflicts if c.rule == "C3") == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.analysis.agents.license_rules'`

- [ ] **Step 3: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/agents/license_rules.py
"""Simplified C1/C2/C3 conflict rules over a (project, dependency) license
pair, per the term model in Liu et al. (arXiv:2401.10636):

- C1: project's license CAN something the dependency's license marks CANNOT.
- C2: dependency's license MUSTs an obligation the project doesn't fulfill.
- C3: copyleft contagion — dependency requires derivative works to carry the
  same license, and the project's license doesn't preserve that.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.main_graph.subgraphs.analysis.agents.license_data import LicenseEntry


@dataclass(frozen=True)
class Conflict:
    rule: str  # "C1" | "C2" | "C3"
    severity: str  # "medium" | "low" | "high"
    detail: str


_C1_FIELDS = {
    "sublicense": "sublicensing",
    "commercial_use": "commercial use",
}
_C2_FIELDS = {
    "include_notice": "retain the original copyright/license notice",
    "disclose_source": "disclose the source of modifications",
    "state_changes": "state what changes were made to the code",
}
_C3_CATEGORIES = ("strong_copyleft", "network_copyleft")


def _check_c1(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> list[Conflict]:
    conflicts = []
    for field, label in _C1_FIELDS.items():
        if getattr(project, field) == "can" and getattr(dep, field) == "cannot":
            conflicts.append(
                Conflict(
                    rule="C1",
                    severity="medium",
                    detail=(
                        f"Project license {project_id} permits {label}, but "
                        f"dependency license {dep_id} does not grant this right."
                    ),
                )
            )
    return conflicts


def _check_c2(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> list[Conflict]:
    conflicts = []
    for field, label in _C2_FIELDS.items():
        if getattr(dep, field) == "must" and getattr(project, field) != "must":
            conflicts.append(
                Conflict(
                    rule="C2",
                    severity="low",
                    detail=(
                        f"Dependency license {dep_id} requires the project to {label}, "
                        f"but project license {project_id} does not declare this "
                        f"obligation as fulfilled."
                    ),
                )
            )
    return conflicts


def _check_c3(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> Conflict | None:
    if dep.same_license != "must" or dep.category not in _C3_CATEGORIES:
        return None
    if project_id == dep_id or project.category in _C3_CATEGORIES:
        return None
    return Conflict(
        rule="C3",
        severity="high",
        detail=(
            f"Dependency license {dep_id} is copyleft and requires derivative works "
            f"to remain under the same terms, but project license {project_id} does "
            f"not preserve this — copyleft contagion risk."
        ),
    )


def check_conflicts(
    project_id: str, project: LicenseEntry, dep_id: str, dep: LicenseEntry
) -> list[Conflict]:
    conflicts = _check_c1(project_id, project, dep_id, dep)
    conflicts.extend(_check_c2(project_id, project, dep_id, dep))
    c3 = _check_c3(project_id, project, dep_id, dep)
    if c3:
        conflicts.append(c3)
    return conflicts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_rules.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/license_rules.py apps/backend/tests/unit/test_license_rules.py
git commit -m "feat: add C1/C2/C3 license conflict rule engine"
```

---

## Task 3: License collection (`license_collector.py`)

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/agents/license_collector.py`
- Modify: `apps/backend/src/utils/config.py` (add `license_lookup_concurrency` setting)
- Test: `apps/backend/tests/unit/test_license_collector.py`

**Interfaces:**
- Consumes: `PrepResult` (`src/models/results.py`, existing), `_npm_metadata` (`src/main_graph/tools/external_api.py:35`, existing, already cached per-process), `settings` (`src/utils/config.py`, existing).
- Produces: `async def collect_licenses(prep: PrepResult) -> dict[str, str]` — maps `"name@version"` package key to raw license string, `"UNKNOWN"` if unresolved. Consumed by Task 4 (`license_agent.py`).

- [ ] **Step 1: Add the concurrency setting**

Edit `apps/backend/src/utils/config.py`, adding after `vuln_min_severity` (currently the last field before `settings = Settings()`):

```python
    # Values: low | medium | high | critical  (default: high)
    vuln_min_severity: str = "high"
    # Cap on concurrent npm registry lookups when resolving licenses for
    # packages whose lockfile doesn't carry a "license" field (yarn/pnpm
    # always; npm when the field is missing) — caps concurrency, not
    # coverage, to avoid hammering the registry on large trees.
    license_lookup_concurrency: int = 10
```

- [ ] **Step 2: Write the failing test**

```python
# apps/backend/tests/unit/test_license_collector.py
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.models.results import PrepResult


def _prep(package_manager: str, packages: dict, repo_path: str = "/tmp/r") -> PrepResult:
    return PrepResult(
        job_id="j1",
        repo_path=repo_path,
        project_metadata={},
        manifest_files=[],
        detected_package_manager=package_manager,
        dependency_graph={"direct": {}, "packages": packages},
        discovery_summary="s",
        vector_store_id="vs1",
    )


def test_collect_licenses_returns_empty_when_no_packages():
    from src.main_graph.subgraphs.analysis.agents.license_collector import (
        collect_licenses,
    )

    result = asyncio.run(collect_licenses(_prep("npm", {})))
    assert result == {}


@pytest.mark.asyncio
async def test_npm_lockfile_license_extraction(tmp_path):
    from src.main_graph.subgraphs.analysis.agents import license_collector

    lock = {
        "packages": {
            "": {},
            "node_modules/express": {"version": "4.18.0", "license": "MIT"},
            "node_modules/lodash": {"version": "4.17.21", "license": "MIT"},
            "node_modules/no-license-field": {"version": "1.0.0"},
        }
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock))

    prep = _prep(
        "npm",
        {
            "express@4.18.0": {},
            "lodash@4.17.21": {},
            "no-license-field@1.0.0": {},
        },
        repo_path=str(tmp_path),
    )

    metadata = AsyncMock(return_value={"error": "not found"})
    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result["express@4.18.0"] == "MIT"
    assert result["lodash@4.17.21"] == "MIT"
    assert result["no-license-field@1.0.0"] == "UNKNOWN"  # missing field, registry also failed
    metadata.assert_awaited_once_with("no-license-field")  # only the missing one falls back


@pytest.mark.asyncio
async def test_yarn_pnpm_falls_back_to_registry_without_reading_lockfile():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("yarn", {"left-pad@1.3.0": {}})
    metadata = AsyncMock(return_value={"license": "WTFPL"})

    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result == {"left-pad@1.3.0": "WTFPL"}
    metadata.assert_awaited_once_with("left-pad")


@pytest.mark.asyncio
async def test_registry_license_field_as_legacy_object_shape():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("pnpm", {"old-pkg@1.0.0": {}})
    metadata = AsyncMock(return_value={"license": {"type": "MIT"}})

    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result == {"old-pkg@1.0.0": "MIT"}


@pytest.mark.asyncio
async def test_unresolvable_package_recorded_as_unknown():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("pnpm", {"ghost@0.0.1": {}})
    metadata = AsyncMock(return_value={"error": "404"})

    with patch.object(license_collector, "_npm_metadata", metadata):
        result = await license_collector.collect_licenses(prep)

    assert result == {"ghost@0.0.1": "UNKNOWN"}


@pytest.mark.asyncio
async def test_registry_lookups_bounded_by_concurrency_setting():
    from src.main_graph.subgraphs.analysis.agents import license_collector

    prep = _prep("pnpm", {f"pkg{i}@1.0.0": {} for i in range(5)})
    in_flight = 0
    max_in_flight = 0

    async def fake_metadata(name: str) -> dict:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return {"license": "MIT"}

    with (
        patch.object(license_collector, "_npm_metadata", fake_metadata),
        patch.object(license_collector.settings, "license_lookup_concurrency", 2),
    ):
        await license_collector.collect_licenses(prep)

    assert max_in_flight <= 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.analysis.agents.license_collector'`

- [ ] **Step 4: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/agents/license_collector.py
"""Collects each dependency's raw license string.

npm's package-lock.json carries a "license" field per installed package,
mirrored from that package's own package.json, without needing an install
(see spec: 179/377 packages had the field in a sample lockfile). yarn.lock
and pnpm-lock.yaml don't carry this metadata, so those — plus any npm entry
missing the field — fall back to the npm registry packument.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from src.main_graph.tools.external_api import _npm_metadata
from src.models.results import PrepResult
from src.utils.config import settings

logger = logging.getLogger(__name__)


def _read_npm_lockfile_licenses(repo_path: str) -> dict[str, str]:
    path = os.path.join(repo_path, "package-lock.json")
    try:
        with open(path) as f:
            lock = json.load(f)
    except Exception as exc:
        logger.warning("license_collector: failed to read package-lock.json: %s", exc)
        return {}
    licenses: dict[str, str] = {}
    for key, entry in (lock.get("packages") or {}).items():
        if key == "" or "node_modules/" not in key:
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        lic = entry.get("license")
        if lic:
            licenses[name] = lic
    return licenses


async def _resolve_via_registry(keys: list[str]) -> dict[str, str]:
    sem = asyncio.Semaphore(settings.license_lookup_concurrency)

    async def fetch(key: str) -> tuple[str, str | None]:
        name = key.rsplit("@", 1)[0]
        async with sem:
            meta = await _npm_metadata(name)
        if "error" in meta:
            return key, None
        lic = meta.get("license")
        if isinstance(lic, dict):  # legacy {"type": "MIT"} shape
            lic = lic.get("type")
        return key, lic

    results = await asyncio.gather(*[fetch(k) for k in keys])
    return {key: lic for key, lic in results if lic}


async def collect_licenses(prep: PrepResult) -> dict[str, str]:
    """Return {"name@version": raw_license_string} for every package in
    prep.dependency_graph["packages"]. Unresolved packages map to "UNKNOWN"
    — never guessed."""
    packages = prep.dependency_graph.get("packages", {})
    if not packages:
        return {}

    licenses: dict[str, str] = {}
    if prep.detected_package_manager == "npm":
        lockfile_licenses = _read_npm_lockfile_licenses(prep.repo_path)
        missing = []
        for key in packages:
            name = key.rsplit("@", 1)[0]
            lic = lockfile_licenses.get(name)
            if lic:
                licenses[key] = lic
            else:
                missing.append(key)
    else:
        missing = list(packages.keys())

    if missing:
        licenses.update(await _resolve_via_registry(missing))
        for key in missing:
            licenses.setdefault(key, "UNKNOWN")

    return licenses
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_collector.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/utils/config.py apps/backend/src/main_graph/subgraphs/analysis/agents/license_collector.py apps/backend/tests/unit/test_license_collector.py
git commit -m "feat: add license collector with npm lockfile + registry fallback"
```

---

## Task 4: License agent (`license_agent.py`)

**Files:**
- Create: `apps/backend/src/main_graph/subgraphs/analysis/agents/license_agent.py`
- Test: `apps/backend/tests/unit/test_license_agent.py`

**Interfaces:**
- Consumes: `BaseAgent` (`base_agent.py:275`, existing), `collect_licenses` (Task 3), `LICENSES`/`resolve`/`UNLICENSED_ID` (Task 1), `check_conflicts` (Task 2), `_load_pkg` (`src/main_graph/tools/package_files.py:17`, existing — same reuse pattern as `external_api.py`'s `from src.main_graph.tools.package_files import _all_deps, _load_pkg`), `FindingNote`/`EvidenceRef` (`src/models/conductor.py`, existing), `AgentDispatch`/`EvidenceBundle`/`PrepResult` (`src/models/results.py`, existing).
- Produces: `class LicenseAgent(BaseAgent)` with `agent_type = "license_agent"`. Consumed by Task 5 (`registry.py`).

- [ ] **Step 1: Write the failing test**

```python
# apps/backend/tests/unit/test_license_agent.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.models.results import AgentDispatch, EvidenceBundle, PrepResult


def _prep() -> PrepResult:
    return PrepResult(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph={
            "direct": {},
            "packages": {
                "gpl-lib@1.0.0": {},
                "mit-lib@2.0.0": {},
                "mystery-lib@3.0.0": {},
                "no-license@4.0.0": {},
            },
        },
        discovery_summary="s",
        vector_store_id="vs1",
    )


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="licenses",
        hypothesis="check for license conflicts",
        packages_to_focus=["express"],  # must be ignored
        agent_type="license_agent",
    )


@pytest.mark.asyncio
async def test_license_agent_run_end_to_end():
    from src.main_graph.subgraphs.analysis.agents import license_agent

    collected = {
        "gpl-lib@1.0.0": "GPL-3.0-only",
        "mit-lib@2.0.0": "MIT",
        "mystery-lib@3.0.0": "Some Custom License Text",
        "no-license@4.0.0": "UNKNOWN",
    }

    with (
        patch.object(
            license_agent, "collect_licenses", AsyncMock(return_value=collected)
        ),
        patch.object(license_agent, "_load_pkg", return_value={"license": "MIT"}),
    ):
        bundle, tools_used, react_iterations = await license_agent.LicenseAgent().run(
            _dispatch(), _prep()
        )

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.packages_to_focus == []  # packages_to_focus ignored
    assert bundle.confidence == 1.0
    assert tools_used == ["license_collector", "license_rules"]
    assert react_iterations == 1

    by_dep = {}
    for f in bundle.findings:
        by_dep.setdefault(f.dep_name, []).append(f)

    # gpl-lib: C1 (medium), C2 x2 (low), C3 (high) against MIT project
    gpl_severities = sorted(f.severity for f in by_dep["gpl-lib"])
    assert gpl_severities == ["high", "low", "low", "medium"]

    # mit-lib vs MIT project: no conflicts
    assert "mit-lib" not in by_dep

    # unresolvable expression -> info finding, manual review
    assert len(by_dep["mystery-lib"]) == 1
    assert by_dep["mystery-lib"][0].severity == "info"
    assert "curated" in by_dep["mystery-lib"][0].description

    # UNKNOWN license -> info finding
    assert len(by_dep["no-license"]) == 1
    assert by_dep["no-license"][0].severity == "info"

    # most severe first
    assert bundle.findings[0].severity == "high"


@pytest.mark.asyncio
async def test_license_agent_treats_missing_project_license_as_unlicensed():
    from src.main_graph.subgraphs.analysis.agents import license_agent

    collected = {"mit-lib@2.0.0": "MIT"}
    with (
        patch.object(
            license_agent, "collect_licenses", AsyncMock(return_value=collected)
        ),
        patch.object(license_agent, "_load_pkg", return_value={}),  # no "license" field
    ):
        bundle, _, _ = await license_agent.LicenseAgent().run(_dispatch(), _prep())

    # MIT dependency musts include_notice; UNLICENSED project doesn't fulfill it -> C2
    assert any(f.severity == "low" and "notice" in f.description for f in bundle.findings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.main_graph.subgraphs.analysis.agents.license_agent'`

- [ ] **Step 3: Write the implementation**

```python
# apps/backend/src/main_graph/subgraphs/analysis/agents/license_agent.py
from __future__ import annotations

import logging

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.subgraphs.analysis.agents.license_collector import collect_licenses
from src.main_graph.subgraphs.analysis.agents.license_data import (
    LICENSES,
    UNLICENSED_ID,
    resolve,
)
from src.main_graph.subgraphs.analysis.agents.license_rules import check_conflicts
from src.main_graph.tools.package_files import _load_pkg
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}


class LicenseAgent(BaseAgent):
    """Deterministic agent: one rule computation over the whole tree, not an
    exploratory investigation — mirrors VulnerabilityAgent since legal-risk
    findings should not depend on an LLM's compatibility judgment.
    packages_to_focus is ignored: a single copyleft transitive dependency
    matters regardless of which packages the conductor asked about.
    """

    agent_type = "license_agent"
    description = (
        "Analyzes license compatibility across the ENTIRE dependency tree against "
        "the project's own license: rights conflicts, obligation gaps, and copyleft "
        "contagion. Covers all direct and transitive packages in a single run, so "
        "packages_to_focus is ignored. Use when the concern involves license "
        "compliance, copyleft, or legal risk."
    )
    system_prompt = ""  # unused: run() does not invoke the LLM

    def _agent_tools(self) -> list:
        return []

    async def run(
        self,
        dispatch: AgentDispatch,
        prep: PrepResult,
        container: ContainerRunPort | None = None,
    ) -> tuple[EvidenceBundle, list[str], int]:
        pkg = _load_pkg(prep.repo_path)
        project_license_str = pkg.get("license") or UNLICENSED_ID
        project_resolved = resolve(project_license_str)
        project_id, project_entry = (
            project_resolved
            if project_resolved is not None
            else (UNLICENSED_ID, LICENSES[UNLICENSED_ID])
        )

        licenses = await collect_licenses(prep)
        findings: list[FindingNote] = []
        for key, raw_license in licenses.items():
            dep_name = key.rsplit("@", 1)[0]
            if raw_license == "UNKNOWN":
                findings.append(
                    FindingNote(
                        dep_name=dep_name,
                        severity="info",
                        description=(
                            "No license could be resolved for this dependency "
                            "(checked lockfile and npm registry) — manual review "
                            "required."
                        ),
                        evidence=[
                            EvidenceRef(
                                tool="license_collector",
                                url=None,
                                log_snippet=f"package={key}",
                            )
                        ],
                    )
                )
                continue

            resolved = resolve(raw_license)
            if resolved is None:
                findings.append(
                    FindingNote(
                        dep_name=dep_name,
                        severity="info",
                        description=(
                            f"License expression '{raw_license}' is not in the "
                            f"curated license table — manual review required."
                        ),
                        evidence=[
                            EvidenceRef(
                                tool="license_collector",
                                url=None,
                                log_snippet=f"package={key} license={raw_license}",
                            )
                        ],
                    )
                )
                continue

            dep_id, dep_entry = resolved
            for conflict in check_conflicts(project_id, project_entry, dep_id, dep_entry):
                findings.append(
                    FindingNote(
                        dep_name=dep_name,
                        severity=conflict.severity,
                        description=conflict.detail,
                        evidence=[
                            EvidenceRef(
                                tool="license_rules",
                                url=None,
                                log_snippet=(
                                    f"{conflict.rule}: project={project_id} "
                                    f"dep={dep_id}"
                                ),
                            )
                        ],
                    )
                )

        findings.sort(key=lambda f: _SEVERITY_RANK.get(f.severity, 0), reverse=True)

        logger.info(
            "license_agent: checked %d package(s) against project license %s, "
            "%d finding(s)",
            len(licenses),
            project_id,
            len(findings),
        )

        bundle = EvidenceBundle(
            domain=dispatch.domain,
            hypothesis=dispatch.hypothesis,
            packages_to_focus=[],
            findings=findings,
            summary=(
                f"Checked license compatibility for {len(licenses)} package(s) "
                f"against project license {project_id}. {len(findings)} finding(s)."
            ),
            confidence=1.0,
        )
        return bundle, ["license_collector", "license_rules"], 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/test_license_agent.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/license_agent.py apps/backend/tests/unit/test_license_agent.py
git commit -m "feat: add LicenseAgent"
```

---

## Task 5: Wire into the registry and conductor

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/agents/registry.py`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`
- Modify: `apps/backend/tests/unit/test_base_agent.py` (registry test)
- Modify: `apps/backend/tests/unit/test_analysis_conductor.py` (prompt test)

**Interfaces:**
- Consumes: `LicenseAgent` (Task 4).

- [ ] **Step 1: Write the failing tests**

In `apps/backend/tests/unit/test_base_agent.py`, update `test_registry_has_expected_agents`:

```python
def test_registry_has_expected_agents():
    from src.main_graph.subgraphs.analysis.agents.registry import REGISTRY

    assert "vulnerability_agent" in REGISTRY
    assert "maintenance_agent" in REGISTRY
    assert "supply_chain_agent" in REGISTRY
    assert "web_research_agent" in REGISTRY
    assert "license_agent" in REGISTRY
```

Append to `apps/backend/tests/unit/test_analysis_conductor.py`:

```python
def test_system_prompt_mentions_license_agent_dispatch_strategy():
    from src.main_graph.subgraphs.analysis.nodes.analysis_conductor import _build_system

    system = _build_system(4)
    # appears once via the auto-generated roster, and once more via the
    # explicit dispatch-strategy line -- proves the guidance line was added,
    # not just agent registration
    assert system.count("license_agent") >= 2
    assert "never shard it" in system
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/test_base_agent.py::test_registry_has_expected_agents tests/unit/test_analysis_conductor.py -v`
Expected: FAIL — `"license_agent" in REGISTRY` is False; `_build_system` doesn't yet mention `license_agent` twice.

- [ ] **Step 3: Wire in the agent**

Edit `apps/backend/src/main_graph/subgraphs/analysis/agents/registry.py`:

```python
from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.subgraphs.analysis.agents.license_agent import LicenseAgent
from src.main_graph.subgraphs.analysis.agents.maintenance_agent import MaintenanceAgent
from src.main_graph.subgraphs.analysis.agents.supply_chain_agent import SupplyChainAgent
from src.main_graph.subgraphs.analysis.agents.vulnerability_agent import (
    VulnerabilityAgent,
)
from src.main_graph.subgraphs.analysis.agents.web_research_agent import WebResearchAgent

REGISTRY: dict[str, type[BaseAgent]] = {
    "vulnerability_agent": VulnerabilityAgent,
    "maintenance_agent": MaintenanceAgent,
    "supply_chain_agent": SupplyChainAgent,
    "web_research_agent": WebResearchAgent,
    "license_agent": LicenseAgent,
}


def get_agent_descriptions() -> dict[str, str]:
    return {k: v.description for k, v in REGISTRY.items()}
```

Edit `apps/backend/src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py`, adding one bullet to `_SYSTEM_TEMPLATE`'s "Dispatch strategy" block, directly after the existing `vulnerability_agent` line:

```python
    - The vulnerability_agent audits the ENTIRE dependency tree in one run. Dispatch
      it at most once, leave packages_to_focus empty for it, and never shard it —
      extra dispatches add no coverage.
    - The license_agent analyzes license compatibility across the ENTIRE dependency
      tree in one run. Dispatch it at most once, leave packages_to_focus empty for
      it, and never shard it — extra dispatches add no coverage.
    - Later iterations: dispatch only to close a specific gap or chase a lead from a
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/test_base_agent.py tests/unit/test_analysis_conductor.py -v`
Expected: PASS (all tests, including the two updated above)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/analysis/agents/registry.py apps/backend/src/main_graph/subgraphs/analysis/nodes/analysis_conductor.py apps/backend/tests/unit/test_base_agent.py apps/backend/tests/unit/test_analysis_conductor.py
git commit -m "feat: register license_agent and add conductor dispatch guidance"
```

---

## Task 6: Remove the superseded `check_licenses` tool

**Files:**
- Modify: `apps/backend/src/main_graph/tools/package_files.py` (remove `check_licenses`)
- Modify: `apps/backend/tests/unit/tools/test_package_files.py` (remove from expected registry list)

**Interfaces:** none — `check_licenses` is not registered on any current agent's toolkit (verified via grep: only referenced in its own definition and in the registry-listing test), so nothing else depends on it.

- [ ] **Step 1: Update the failing test first**

Edit `apps/backend/tests/unit/tools/test_package_files.py`, removing `"check_licenses",` from the `expected` list (currently line 92), so the list reads `..., "install_scripts", "duplicate_packages", ...`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_package_files.py -v`
Expected: FAIL — the test no longer expects `"check_licenses"`, but it is still in `TOOL_REGISTRY`, so... actually this removal doesn't make the test fail on its own (removing an assertion can't fail); this step just prepares the test. Skip the fail-check for this deletion-only test edit and proceed directly to Step 3.

- [ ] **Step 3: Remove the tool**

Edit `apps/backend/src/main_graph/tools/package_files.py`, deleting the entire `check_licenses` function and its `@register(...)` decorator (currently lines 156-187):

```python
@register(
    "check_licenses",
    "Collects licenses for all dependencies and flags non-permissive licenses",
)
async def check_licenses(repo_path: str) -> dict:
    nm_path = os.path.join(repo_path, "node_modules")
    permissive = {
        "mit",
        "isc",
        "bsd-2-clause",
        "bsd-3-clause",
        "apache-2.0",
        "cc0-1.0",
        "0bsd",
        "unlicense",
    }
    results = []
    if os.path.isdir(nm_path):
        for entry in os.listdir(nm_path)[:200]:
            pkg_path = os.path.join(nm_path, entry, "package.json")
            try:
                with open(pkg_path) as f:
                    dep_pkg = json.load(f)
                lic = dep_pkg.get("license", "UNKNOWN")
                is_flagged = str(lic).lower() not in permissive
                results.append(
                    {"package": entry, "license": lic, "flagged": is_flagged}
                )
            except Exception:
                pass
    flagged = [r for r in results if r["flagged"]]
    return {"licenses": results, "flagged_count": len(flagged), "flagged": flagged}


```

(Delete this whole block — the blank line before `@register("duplicate_packages", ...)` should remain, giving the usual single blank line between top-level definitions.)

- [ ] **Step 4: Run tests to verify everything passes**

Run: `cd apps/backend && uv run pytest tests/unit/tools/test_package_files.py -v`
Expected: PASS — `TOOL_REGISTRY` no longer contains `check_licenses`, and the updated expected list no longer checks for it.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/tools/package_files.py apps/backend/tests/unit/tools/test_package_files.py
git commit -m "refactor: remove check_licenses, superseded by license_agent"
```

---

## Final verification

After all 6 tasks:

```bash
cd apps/backend
uv run pytest tests/unit -v
uv run ruff check src tests
uv run mypy src
```

All unit tests should pass; ruff and mypy should report no new issues in the touched files. (`tests/subgraphs/test_analysis_subgraph.py` is marked `subgraph` and requires Docker — not required for this plan, but worth a spot-check run if Docker is available, since it exercises the conductor→domain_agent dispatch path this plan extends.)
