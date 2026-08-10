# Remediation PR Description Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the auto-generated remediation PR description: drop a redundant title line, show severity + description per finding in the "Findings addressed" table, and render verification as a GitHub checklist under a plain "Verification" header.

**Architecture:** Add a `FindingSummary {dep_name, severity, description}` model and thread it alongside the existing `addresses: list[str]` field through `RemediationTarget` → `Remediation`, populated once in `select_remediation_targets` from the `FindingNote` objects it already reads. The PR body template and its `_pr_*` helper functions in `deepagent/nodes.py` are updated to consume the new field and to change formatting (no new LLM/tool calls anywhere in this change).

**Tech Stack:** Python, Pydantic v2, pytest (`pytest-asyncio`), uv, ruff, mypy.

## Global Constraints

- Backend package manager is `uv` — run all commands as `uv run <cmd>` from `apps/backend/`.
- No new LLM calls, no new tool calls, no new external data sources — all data used here (`FindingNote.severity`, `FindingNote.description`) already exists on objects the remediation subgraph already reads.
- `addresses: list[str]` on `RemediationTarget`/`Remediation` is untouched — `finding_summaries` is strictly additive, since `addresses` has other consumers (grouping, targeted re-verify dep list in `pr_and_persist_node`).
- Follow existing code style: `from __future__ import annotations` at the top of touched files (already present), no comments explaining *what* code does, only *why* where non-obvious.
- Spec: `docs/superpowers/specs/2026-08-08-remediation-pr-description-improvements.md` — consult it for the rationale on why category/domain tagging is explicitly out of scope.

---

### Task 1: Add `FindingSummary` model and `finding_summaries` fields

**Files:**
- Modify: `apps/backend/src/models/remediation.py:23-59` (`Remediation`, `RemediationTarget` classes)
- Test: `apps/backend/tests/unit/models/test_remediation_models.py`

**Interfaces:**
- Produces: `FindingSummary(BaseModel)` with fields `dep_name: str`, `severity: str`, `description: str`. `RemediationTarget.finding_summaries: list[FindingSummary]` (default `[]`). `Remediation.finding_summaries: list[FindingSummary]` (default `[]`).

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/models/test_remediation_models.py` (add `FindingSummary` to the existing `from src.models.remediation import (...)` block):

```python
def test_finding_summary_defaults_and_round_trip():
    fs = FindingSummary(
        dep_name="lodash", severity="high", description="prototype pollution"
    )
    assert fs.dep_name == "lodash"
    assert fs.severity == "high"
    assert fs.description == "prototype pollution"


def test_remediation_target_finding_summaries_default_empty():
    t = RemediationTarget(target_dep="lodash", addresses=["lodash"])
    assert t.finding_summaries == []


def test_remediation_finding_summaries_default_empty():
    r = Remediation(addresses=["lodash"], target_dep="lodash")
    assert r.finding_summaries == []


def test_remediation_carries_finding_summaries():
    r = Remediation(
        addresses=["lodash"],
        target_dep="lodash",
        finding_summaries=[
            FindingSummary(
                dep_name="lodash", severity="high", description="proto pollution"
            )
        ],
    )
    assert r.finding_summaries[0].dep_name == "lodash"
    assert r.finding_summaries[0].severity == "high"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'FindingSummary'` (or `NameError` once import is added but field doesn't exist).

- [ ] **Step 3: Implement the model changes**

In `apps/backend/src/models/remediation.py`, add the new class right before `class Remediation` (after `CodeChange`, before line 23):

```python
class FindingSummary(BaseModel):
    dep_name: str
    severity: str
    description: str
```

Then update `Remediation` (currently lines 23-42) to add the field after `addresses`:

```python
class Remediation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    addresses: list[str]  # analysis finding dep_names this covers
    finding_summaries: list[FindingSummary] = Field(default_factory=list)
    target_dep: str  # the DIRECT dep acted on (the anchor)
    required_by: list[str] = Field(default_factory=list)
    strategy: Literal["bump", "bump_with_codemod", "replace"] = "bump"
    from_range: str | None = None
    to_range: str | None = None
    replacement_dep: str | None = None
    replacement_range: str | None = None
    migration_plan: str = ""
    plan: MigrationPlan | None = None  # persisted, reviewable (spec D5)
    code_changes: list[CodeChange] = Field(default_factory=list)
    status: Literal["fixed", "failed", "skipped"] = "skipped"
    skip_reason: str | None = None
    verification: VerificationResult = Field(default_factory=VerificationResult)
    attempts: int = 0
    patch: str = ""
    branch: str | None = None
    pr_url: str | None = None
```

And `RemediationTarget` (currently lines 53-59):

```python
class RemediationTarget(BaseModel):
    """Internal: a deduped unit of work produced by target selection."""

    target_dep: str  # direct dep to bump
    addresses: list[str]  # finding dep_names grouped under it
    finding_summaries: list[FindingSummary] = Field(default_factory=list)
    current_range: str | None = None  # from package.json, if known
    tier: Literal["r1", "r2", "r3"] | None = None  # advisory hint from classify
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/models/test_remediation_models.py -v`
Expected: PASS (all tests, including pre-existing ones — `finding_summaries` is additive so nothing else in this file should break).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/models/remediation.py apps/backend/tests/unit/models/test_remediation_models.py
git commit -m "feat: add FindingSummary model to Remediation and RemediationTarget"
```

---

### Task 2: Populate `finding_summaries` in `select_remediation_targets`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/selection.py:18-41`
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_selection.py`

**Interfaces:**
- Consumes: `FindingSummary` from Task 1 (`from src.models.remediation import FindingSummary, RemediationTarget`).
- Produces: `select_remediation_targets(...)` now also fills `RemediationTarget.finding_summaries` — one `FindingSummary` per finding grouped under that anchor, built from the same `FindingNote` objects used to build `addresses`.

- [ ] **Step 1: Write the failing test**

Add to `apps/backend/tests/unit/subgraphs/remediation/test_selection.py`:

```python
def test_direct_finding_carries_finding_summary():
    targets = select_remediation_targets([_f("lodash")], GRAPH, "high")
    assert len(targets[0].finding_summaries) == 1
    summary = targets[0].finding_summaries[0]
    assert summary.dep_name == "lodash"
    assert summary.severity == "high"
    assert summary.description == "lodash issue"


def test_two_transitives_under_same_direct_unify_finding_summaries():
    graph = {
        "direct": {"parent": "1.0.0"},
        "packages": {
            "parent@1.0.0": {"dependencies": ["a@1", "b@1"]},
            "a@1": {"dependencies": []},
            "b@1": {"dependencies": []},
        },
    }
    targets = select_remediation_targets([_f("a"), _f("b")], graph, "high")
    assert len(targets) == 1
    names = sorted(fs.dep_name for fs in targets[0].finding_summaries)
    assert names == ["a", "b"]


def test_severity_filter_drops_finding_summaries_below_floor():
    targets = select_remediation_targets([_f("lodash", "low")], GRAPH, "high")
    assert targets == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_selection.py -v`
Expected: FAIL — `AttributeError: 'RemediationTarget' object has no attribute 'finding_summaries'` is already fixed by Task 1, so instead this should FAIL on `assert len(targets[0].finding_summaries) == 1` since selection.py doesn't populate it yet (`finding_summaries == []`).

- [ ] **Step 3: Implement the population logic**

In `apps/backend/src/main_graph/subgraphs/remediation/selection.py`, update the imports and `select_remediation_targets`:

```python
from __future__ import annotations

from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)
from src.models.conductor import FindingNote
from src.models.remediation import FindingSummary, RemediationTarget
from src.utils.severity import filter_by_min_severity


def _anchors(graph: dict, dep_name: str) -> list[str]:
    if is_direct(graph, dep_name):
        return [dep_name]
    return direct_dependents(graph, dep_name)


def select_remediation_targets(
    findings: list[FindingNote], dependency_graph: dict, min_severity: str
) -> list[RemediationTarget]:
    """Deterministic: filter by severity, anchor transitives to their direct
    dependent(s), unify findings that share a direct-dep bump.

    Findings with no direct anchor (no lever the user controls) are dropped.
    """
    survivors = filter_by_min_severity(findings, min_severity)
    direct = dependency_graph.get("direct") or {}

    grouped: dict[str, set[str]] = {}
    summaries: dict[str, dict[str, FindingSummary]] = {}
    for finding in survivors:
        for anchor in _anchors(dependency_graph, finding.dep_name):
            grouped.setdefault(anchor, set()).add(finding.dep_name)
            summaries.setdefault(anchor, {})[finding.dep_name] = FindingSummary(
                dep_name=finding.dep_name,
                severity=finding.severity,
                description=finding.description,
            )

    return [
        RemediationTarget(
            target_dep=dep,
            addresses=sorted(addressed),
            finding_summaries=[
                summaries[dep][name] for name in sorted(addressed)
            ],
            current_range=direct.get(dep),
        )
        for dep, addressed in sorted(grouped.items())
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_selection.py -v`
Expected: PASS (all tests, including all pre-existing ones in the file).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/selection.py apps/backend/tests/unit/subgraphs/remediation/test_selection.py
git commit -m "feat: populate finding_summaries in select_remediation_targets"
```

---

### Task 3: Thread `finding_summaries` through `_resolve_working_targets` and `_assemble_remediations`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py:41-55` (`_resolve_working_targets`), `:131-189` (`_assemble_remediations`)
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Consumes: `RemediationTarget.finding_summaries`, `Remediation.finding_summaries` (Task 1).
- Produces: `_assemble_remediations(...)` output dicts now carry `finding_summaries` (copied straight from the source `RemediationTarget`, never recomputed) in all four branches.

- [ ] **Step 1: Write the failing tests**

In `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`, extend the existing `from src.main_graph.subgraphs.remediation.deepagent.nodes import (...)` block (near the top of the file) to also pull in the two private functions under test:

```python
from src.main_graph.subgraphs.remediation.deepagent.nodes import (
    _assemble_remediations,
    _resolve_working_targets,
    group_and_verify_gate,
    pr_and_persist_node,
    remediate_targets_node,
    route_after_group_verify,
)
```

Also add `FindingSummary` to the existing `from src.models.remediation import (...)` block in the same file.

Then add these tests:

```python
def test_resolve_working_targets_retry_synthesizes_empty_finding_summaries():
    prep = _prep(dependency_graph={"direct": {"lodash": "^4.17.11"}, "packages": {}})
    state = {"retry_targets": ["lodash"], "targets": {}}
    out = _resolve_working_targets(state, prep)
    assert out["lodash"]["finding_summaries"] == []


def test_assemble_remediations_carries_finding_summaries_through_no_plan_branch():
    targets = {
        "lodash": RemediationTarget(
            target_dep="lodash",
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash", severity="high", description="proto pollution"
                )
            ],
        ).model_dump()
    }
    out = _assemble_remediations(targets, plans={}, outcomes={}, omit=set())
    assert out["lodash"]["finding_summaries"] == [
        {"dep_name": "lodash", "severity": "high", "description": "proto pollution"}
    ]


def test_assemble_remediations_carries_finding_summaries_through_outcome_branch():
    fs = FindingSummary(dep_name="lodash", severity="high", description="proto pollution")
    targets = {
        "lodash": RemediationTarget(
            target_dep="lodash", addresses=["lodash"], finding_summaries=[fs]
        ).model_dump()
    }
    plans = {
        "lodash": MigrationPlan(target_dep="lodash", tier_hint="r1").model_dump()
    }
    outcomes = {"lodash": RemediationOutcome(to_range="^4.17.21").model_dump()}
    out = _assemble_remediations(targets, plans=plans, outcomes=outcomes, omit=set())
    assert out["lodash"]["finding_summaries"] == [fs.model_dump()]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -k "finding_summaries" -v`
Expected: FAIL — `KeyError: 'finding_summaries'` (`_resolve_working_targets` doesn't set it; `_assemble_remediations` doesn't pass it to `Remediation(...)`).

- [ ] **Step 3: Implement the threading**

In `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`, update `_resolve_working_targets` (lines 41-55):

```python
def _resolve_working_targets(state: RemediationState, prep) -> dict[str, dict]:
    retry_targets = state.get("retry_targets")
    known = state.get("targets") or {}
    if not retry_targets:
        return known
    direct = prep.dependency_graph.get("direct") or {}
    out: dict[str, dict] = {}
    for dep in retry_targets:
        out[dep] = (
            known.get(dep)
            or RemediationTarget(
                target_dep=dep,
                addresses=[],
                finding_summaries=[],
                current_range=direct.get(dep),
            ).model_dump()
        )
    return out
```

Update `_assemble_remediations` (lines 131-189) — add `finding_summaries=target.finding_summaries` to all four `Remediation(...)` constructions:

```python
def _assemble_remediations(
    targets: dict[str, dict],
    plans: dict[str, dict],
    outcomes: dict[str, dict],
    omit: set[str],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dep, target_dict in targets.items():
        if dep in omit:
            continue
        target = RemediationTarget(**target_dict)
        plan_dict = plans.get(dep)
        if plan_dict is None:
            out[dep] = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                from_range=target.current_range,
                status="failed",
                skip_reason="planner produced no MigrationPlan",
            ).model_dump()
            continue
        plan = MigrationPlan(**plan_dict)
        if "replace" in _plan_kinds(plan_dict):
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                strategy="replace",
                from_range=target.current_range,
                status="skipped",
                skip_reason="dependency replacement deferred (Spec B)",
                plan=plan,
            )
        elif dep in outcomes:
            outcome = RemediationOutcome(**outcomes[dep])
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                strategy=outcome.strategy,
                from_range=target.current_range,
                to_range=outcome.to_range,
                replacement_dep=outcome.replacement_dep,
                replacement_range=outcome.replacement_range,
                migration_plan=outcome.migration_plan,
                patch=outcome.code_diff,
                status="skipped",  # provisional; gate sets real status
                skip_reason=outcome.skip_reason,
                plan=plan,
            )
        else:
            rem = Remediation(
                addresses=target.addresses,
                finding_summaries=target.finding_summaries,
                target_dep=dep,
                from_range=target.current_range,
                status="failed",
                skip_reason="execution agent produced no outcome",
                plan=plan,
            )
        out[dep] = rem.model_dump()
    return out
```

Note: `group_and_verify_gate` (`Remediation(**m)`, line ~360) and `pr_and_persist_node` (`Remediation(**r)`, line ~534) need no changes — both rehydrate from dicts that already carry `finding_summaries` once it round-trips through `.model_dump()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v`
Expected: PASS (all tests in the file, including pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "feat: thread finding_summaries through remediation target resolution"
```

---

### Task 4: Drop the redundant title line from `_PR_BODY_TEMPLATE`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py:408-522` (`_PR_BODY_TEMPLATE`, `_pr_title_and_body`)
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Produces: `_PR_BODY_TEMPLATE` no longer starts with a title line or takes a `{label}` placeholder; `_pr_title_and_body`'s `.format()` call drops the now-unused `label=label` kwarg.

- [ ] **Step 1: Write the failing test**

Update `test_pr_title_and_body_bump_case` in `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py` — add this assertion at the top of the body of the test (right after computing `title, body`):

```python
    assert "Automated dependency remediation" not in body
    assert body.startswith("## Summary")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py::test_pr_title_and_body_bump_case -v`
Expected: FAIL — `body.startswith("## Summary")` is False (body currently starts with `"Automated dependency remediation (bump).\n\n## Summary"`).

- [ ] **Step 3: Update the template**

In `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`, replace `_PR_BODY_TEMPLATE` (lines 408-428):

```python
# Reference layout for a generated remediation PR body. Keep new sections
# additive to this shape rather than inventing a one-off format per caller.
_PR_BODY_TEMPLATE = """\
## Summary

{summary}

## Changes

{changes_table}

## Findings addressed

{findings_table}

## Verification

{verification}
{migration_notes}"""
```

And in `_pr_title_and_body` (around line 512), drop the now-unused `label=label` kwarg from the `.format()` call:

```python
    body = _PR_BODY_TEMPLATE.format(
        summary=_pr_summary(group_remediations, label),
        changes_table=_pr_changes_table(group_remediations),
        findings_table=_pr_findings_table(group_remediations),
        verification=_pr_verification_summary(verification),
        migration_notes=f"\n## Migration notes\n\n{migration_notes}\n"
        if migration_notes
        else "",
    )
```

(`label` itself stays a local variable in `_pr_title_and_body` — it's still used for `title_label` and passed into `_pr_summary(group_remediations, label)`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py::test_pr_title_and_body_bump_case tests/unit/subgraphs/remediation/test_deepagent_nodes.py::test_pr_title_and_body_replace_case_includes_migration_notes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "fix: drop redundant title line from remediation PR body template"
```

---

### Task 5: Enrich `_pr_findings_table` with severity and description

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py:471-480` (`_pr_findings_table`) — add a `_truncate` helper alongside it
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Consumes: `Remediation.finding_summaries` (Task 1/3), `Remediation.addresses`.
- Produces: `_pr_findings_table(...)` now renders `| Finding | Severity | Description | Resolved by |`; new private helper `_truncate(text: str, limit: int = 150) -> str`.

- [ ] **Step 1: Write the failing test**

Update `test_pr_title_and_body_bump_case` in `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py` — change the `Remediation(...)` construction to include `finding_summaries`, and update the findings-table assertion:

```python
def test_pr_title_and_body_bump_case():
    members = [
        Remediation(
            id="r1",
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(
                    dep_name="lodash",
                    severity="high",
                    description="known prototype pollution vulnerability",
                )
            ],
            target_dep="lodash",
            strategy="bump",
            from_range="^4.17.11",
            to_range="^4.17.21",
            status="fixed",
        )
    ]
    verification = VerificationResult(
        installed=True, tested=True, finding_resolved=True
    )

    title, body = deepagent_nodes._pr_title_and_body(members, verification)

    assert "Automated dependency remediation" not in body
    assert body.startswith("## Summary")
    assert title == "Remediate lodash (bump)"
    assert "please review before merging" not in body
    assert "| lodash | bump | `^4.17.11` -> `^4.17.21` | - |" in body
    assert (
        "| lodash | high | known prototype pollution vulnerability | lodash |"
        in body
    )
    assert "- [x] Install" in body
    assert "- [x] Tests" in body
    assert "- [x] Audit re-scan: finding no longer present" in body
    assert "## Migration notes" not in body
```

Add a new focused test for truncation directly against the helper:

```python
def test_pr_findings_table_truncates_long_description():
    long_desc = "x" * 300
    members = [
        Remediation(
            addresses=["lodash"],
            finding_summaries=[
                FindingSummary(dep_name="lodash", severity="high", description=long_desc)
            ],
            target_dep="lodash",
        )
    ]
    table = deepagent_nodes._pr_findings_table(members)
    row = next(line for line in table.splitlines() if line.startswith("| lodash"))
    cell = row.split(" | ")[2]
    assert len(cell) <= 150
    assert cell.endswith("…")


def test_pr_findings_table_none_when_no_addresses():
    members = [Remediation(addresses=[], target_dep="lodash")]
    assert deepagent_nodes._pr_findings_table(members) == "None."


def test_pr_findings_table_dash_when_no_summary_for_finding():
    members = [Remediation(addresses=["lodash"], target_dep="lodash")]
    table = deepagent_nodes._pr_findings_table(members)
    assert "| lodash | - | - | lodash |" in table
```

Also import `FindingSummary` alongside the other remediation model imports at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -k "findings_table or pr_title_and_body_bump" -v`
Expected: FAIL — `AttributeError: module 'src.main_graph.subgraphs.remediation.deepagent.nodes' has no attribute '_truncate'` once referenced, and the table-format assertions fail against the current 2-column output.

- [ ] **Step 3: Implement the enriched table**

In `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`, add the import (`FindingSummary` alongside the existing `from src.models.remediation import (...)` block) and replace `_pr_findings_table` (currently lines 471-480):

```python
def _truncate(text: str, limit: int = 150) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _pr_findings_table(group_remediations: list[Remediation]) -> str:
    summaries: dict[str, FindingSummary] = {
        fs.dep_name: fs
        for r in group_remediations
        for fs in r.finding_summaries
    }
    rows = []
    for r in group_remediations:
        for finding in r.addresses or [r.target_dep]:
            summary = summaries.get(finding)
            severity = summary.severity if summary else "-"
            description = _truncate(summary.description) if summary else "-"
            rows.append(
                f"| {finding} | {severity} | {description} | {r.target_dep} |"
            )
    if not rows:
        return "None."
    header = (
        "| Finding | Severity | Description | Resolved by |\n"
        "| --- | --- | --- | --- |"
    )
    return "\n".join([header, *rows])
```

Update the `from src.models.remediation import (...)` block at the top of `nodes.py` to include `FindingSummary`:

```python
from src.models.remediation import (
    FindingSummary,
    MigrationPlan,
    Remediation,
    RemediationOutcome,
    RemediationResult,
    RemediationTarget,
    VerificationResult,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "feat: show severity and description per finding in remediation PR body"
```

---

### Task 6: Render verification as a checklist under a plain "Verification" header

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py:483-496` (`_pr_verification_summary`)
- Test: `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py`

**Interfaces:**
- Produces: `_pr_verification_summary(...)` now renders GitHub task-list checkboxes; new private helper `_checkbox(passed: bool, label: str) -> str`.

- [ ] **Step 1: Write the failing test**

Update `test_pr_title_and_body_replace_case_includes_migration_notes` in `apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py` — change the verification assertion:

```python
    assert "- [ ] Audit re-scan (failed)" not in body
    assert "- [ ] Audit re-scan: finding still present" in body
```

Add a focused test directly against the helper for the full checkbox matrix:

```python
def test_pr_verification_summary_all_passed():
    v = VerificationResult(installed=True, built=True, tested=True, finding_resolved=True)
    summary = deepagent_nodes._pr_verification_summary(v)
    assert summary == (
        "- [x] Install\n"
        "- [x] Build\n"
        "- [x] Tests\n"
        "- [x] Audit re-scan: finding no longer present"
    )


def test_pr_verification_summary_failure_and_omitted_fields():
    v = VerificationResult(installed=True, built=None, tested=False, finding_resolved=None)
    summary = deepagent_nodes._pr_verification_summary(v)
    assert summary == "- [x] Install\n- [ ] Tests (failed)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -k "verification_summary or replace_case" -v`
Expected: FAIL — current output uses `"- Install: passed"` prose, not checkboxes.

- [ ] **Step 3: Implement the checklist rendering**

In `apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py`, replace `_pr_verification_summary` (currently lines 483-496):

```python
def _checkbox(passed: bool, label: str) -> str:
    return f"- [x] {label}" if passed else f"- [ ] {label} (failed)"


def _pr_verification_summary(verification: VerificationResult) -> str:
    lines = [_checkbox(verification.installed, "Install")]
    if verification.built is not None:
        lines.append(_checkbox(verification.built, "Build"))
    if verification.tested is not None:
        lines.append(_checkbox(verification.tested, "Tests"))
    if verification.finding_resolved is not None:
        resolved = (
            "finding no longer present"
            if verification.finding_resolved
            else "finding still present"
        )
        box = "x" if verification.finding_resolved else " "
        lines.append(f"- [{box}] Audit re-scan: {resolved}")
    return "\n".join(lines)
```

The header rename (`## Verification (sandboxed container)` → `## Verification`) was already done as part of Task 4's template rewrite — no further template change needed here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/remediation/test_deepagent_nodes.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/main_graph/subgraphs/remediation/deepagent/nodes.py apps/backend/tests/unit/subgraphs/remediation/test_deepagent_nodes.py
git commit -m "feat: render remediation PR verification section as a checklist"
```

---

### Task 7: Full verification pass

**Files:** None (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `cd apps/backend && uv run pytest`
Expected: All tests PASS, no failures or errors anywhere in the suite (not just the files touched above — confirms nothing else constructs `Remediation`/`RemediationTarget` in a way this change breaks).

- [ ] **Step 2: Run the linter**

Run: `cd apps/backend && uv run ruff check .`
Expected: No lint errors in touched files.

- [ ] **Step 3: Run the type checker**

Run: `cd apps/backend && uv run mypy .`
Expected: No new type errors introduced by this change (pre-existing unrelated errors, if any, are out of scope).

- [ ] **Step 4: Manually inspect one generated PR body**

In a Python shell or scratch script, call `deepagent_nodes._pr_title_and_body` with a realistic `Remediation` (including `finding_summaries`) and a `VerificationResult`, print the body, and visually confirm: no leading title line, 4-column findings table with real severity/description, `## Verification` header with checkboxes.

- [ ] **Step 5: Commit (if Step 4 required any fixes)**

Only if manual inspection in Step 4 surfaced a formatting issue requiring a fix — otherwise this task ends at Step 4 with nothing to commit.
