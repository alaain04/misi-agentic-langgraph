# Direct-Dependency-Anchored Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every risk-finding recommendation actionable by anchoring it on a direct dependency, and stop emitting quality-proxy (maintenance) findings for transitive dependencies.

**Architecture:** Add two pure helpers over the existing flat `dependency_graph` (`is_direct`, `direct_dependents`). The `maintenance_agent` uses `is_direct` to deterministically drop transitive findings. The report `finding_enricher_agent` computes `is_direct`/`direct_dependents` from `prep.dependency_graph`, stamps them on the `ReportFinding` deterministically, and threads a directness rule into its system prompt so recommendations always target a direct dependency. `dep_name` stays the issue's true location for identity/dedup.

**Tech Stack:** Python 3.12, LangGraph, Pydantic v2, pytest / pytest-asyncio, ruff, mypy, uv.

## Global Constraints

- Package manager: `uv` (run tools as `uv run <cmd>`), never pip.
- No emoji in code, comments, or commit messages.
- Backend only — do not touch `apps/frontend`.
- Deterministic enforcement over prompt-only: the maintenance filter and the `is_direct`/`direct_dependents` values are set in code; prompt edits are additive.
- Preserve `dep_name` as the package where the issue physically is — do NOT re-key findings onto the direct dependency.
- Before claiming done: run `uv run pytest`, `uv run ruff check .`, `uv run mypy src` from `apps/backend` and show output.
- All commands below run from `apps/backend/` (i.e. `apps/v3/langgraph/apps/backend`).

---

### Task 1: Graph helpers `is_direct` and `direct_dependents`

**Files:**
- Modify: `src/main_graph/subgraphs/discovery/dependency_graph.py` (append after `count_dependencies`, currently ending line 59)
- Test: `tests/unit/test_dependency_graph_helpers.py` (create)

**Interfaces:**
- Consumes: the graph shape produced by `build_dependency_graph` — `{"direct": {name: version}, "packages": {"name@version": {"version": str, "dependencies": ["child_name@version", ...]}}}`.
- Produces:
  - `is_direct(graph: dict, name: str) -> bool`
  - `direct_dependents(graph: dict, name: str) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dependency_graph_helpers.py`:

```python
from __future__ import annotations

from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)

_GRAPH = {
    "direct": {"express": "4.18.0", "webpack": "5.0.0"},
    "packages": {
        "express@4.18.0": {
            "version": "4.18.0",
            "dependencies": ["body-parser@1.20.0"],
        },
        "body-parser@1.20.0": {
            "version": "1.20.0",
            "dependencies": ["qs@6.11.0"],
        },
        "qs@6.11.0": {"version": "6.11.0", "dependencies": []},
        "webpack@5.0.0": {"version": "5.0.0", "dependencies": ["qs@6.11.0"]},
    },
}


def test_is_direct_true_for_declared_dependency():
    assert is_direct(_GRAPH, "express") is True


def test_is_direct_false_for_transitive():
    assert is_direct(_GRAPH, "qs") is False


def test_is_direct_false_when_absent():
    assert is_direct(_GRAPH, "not-installed") is False


def test_direct_dependents_empty_for_direct_dependency():
    assert direct_dependents(_GRAPH, "express") == []


def test_direct_dependents_single_parent():
    assert direct_dependents(_GRAPH, "body-parser") == ["express"]


def test_direct_dependents_shared_transitive_lists_all_sorted():
    # qs is pulled by express (via body-parser) and directly by webpack
    assert direct_dependents(_GRAPH, "qs") == ["express", "webpack"]


def test_direct_dependents_scoped_package_name():
    graph = {
        "direct": {"@nestjs/core": "10.0.0"},
        "packages": {
            "@nestjs/core@10.0.0": {
                "version": "10.0.0",
                "dependencies": ["@scope/leaf@1.0.0"],
            },
            "@scope/leaf@1.0.0": {"version": "1.0.0", "dependencies": []},
        },
    }
    assert direct_dependents(graph, "@scope/leaf") == ["@nestjs/core"]


def test_direct_dependents_empty_when_no_transitive_data():
    # package.json fallback: direct names but no packages graph
    graph = {"direct": {"lodash": "^4.17.21"}, "packages": {}}
    assert direct_dependents(graph, "some-transitive") == []


def test_direct_dependents_empty_graph():
    assert direct_dependents({}, "anything") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dependency_graph_helpers.py -v`
Expected: FAIL with `ImportError: cannot import name 'direct_dependents'`

- [ ] **Step 3: Implement the helpers**

Append to `src/main_graph/subgraphs/discovery/dependency_graph.py` (after `count_dependencies`, before `build_dependency_graph`):

```python
def is_direct(graph: dict, name: str) -> bool:
    """True if `name` is a declared direct dependency in this graph."""
    return name in (graph.get("direct") or {})


def _package_name(flat_key: str) -> str:
    """Recover the package name from a "name@version" graph key, tolerating
    scoped names like "@scope/pkg@1.2.3"."""
    return flat_key.rsplit("@", 1)[0]


def direct_dependents(graph: dict, name: str) -> list[str]:
    """Return the direct dependencies whose subtree pulls in `name`, sorted.

    Empty when `name` is itself a direct dependency, or when the flat graph
    has no transitive data (e.g. package.json fallback) to trace edges
    through. Walks the recorded `packages` edges upward from every installed
    version of `name` to whichever direct-dependency roots reach it, so a
    transitive shared by several direct deps lists all of them.
    """
    direct = graph.get("direct") or {}
    if name in direct:
        return []
    packages = graph.get("packages") or {}
    if not packages:
        return []

    direct_keys = {f"{n}@{v}" for n, v in direct.items()}
    parents: dict[str, set[str]] = {}
    for key, info in packages.items():
        for child in info.get("dependencies", []):
            parents.setdefault(child, set()).add(key)

    result: set[str] = set()
    seen: set[str] = set()
    stack = [k for k in packages if _package_name(k) == name]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        if key in direct_keys:
            result.add(_package_name(key))
        stack.extend(parents.get(key, ()))
    return sorted(result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dependency_graph_helpers.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Lint and type-check the touched files**

Run: `uv run ruff check src/main_graph/subgraphs/discovery/dependency_graph.py tests/unit/test_dependency_graph_helpers.py && uv run mypy src/main_graph/subgraphs/discovery/dependency_graph.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/discovery/dependency_graph.py tests/unit/test_dependency_graph_helpers.py
git commit -m "feat: add is_direct and direct_dependents graph helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

### Task 2: Maintenance agent drops transitive findings

**Files:**
- Modify: `src/main_graph/subgraphs/analysis/agents/maintenance_agent.py`
- Test: `tests/unit/test_maintenance_agent.py` (create)

**Interfaces:**
- Consumes: `is_direct` from Task 1; `BaseAgent.run(dispatch, prep, container) -> tuple[EvidenceBundle, list[str], int]`.
- Produces: `MaintenanceAgent.run` with the same signature, filtering `bundle.findings` to direct-only when the graph has direct data.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_maintenance_agent.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.subgraphs.analysis.agents.maintenance_agent import MaintenanceAgent
from src.models.conductor import FindingNote
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult

_GRAPH = {
    "direct": {"express": "4.18.0"},
    "packages": {
        "express@4.18.0": {"version": "4.18.0", "dependencies": ["qs@6.11.0"]},
        "qs@6.11.0": {"version": "6.11.0", "dependencies": []},
    },
}


def _prep(graph: dict) -> PrepResult:
    return PrepResult(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=[],
        detected_package_manager="npm",
        dependency_graph=graph,
        discovery_summary="s",
        vector_store_id="",
    )


def _dispatch() -> AgentDispatch:
    return AgentDispatch(
        domain="maintenance",
        hypothesis="check stale deps",
        packages_to_focus=["express", "qs"],
        agent_type="maintenance_agent",
    )


def _bundle(findings: list[FindingNote]) -> EvidenceBundle:
    return EvidenceBundle(
        domain="maintenance",
        hypothesis="h",
        packages_to_focus=["express", "qs"],
        findings=findings,
        summary="s",
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_maintenance_drops_transitive_findings():
    findings = [
        FindingNote(dep_name="express", severity="medium", description="stale", evidence=[]),
        FindingNote(dep_name="qs", severity="medium", description="stale", evidence=[]),
    ]
    with patch.object(
        MaintenanceAgent.__bases__[0], "run",
        AsyncMock(return_value=(_bundle(findings), ["unmaintained_packages"], 1)),
    ):
        bundle, tools, iters = await MaintenanceAgent().run(_dispatch(), _prep(_GRAPH))

    names = [f.dep_name for f in bundle.findings]
    assert names == ["express"]  # qs (transitive) dropped
    assert tools == ["unmaintained_packages"]


@pytest.mark.asyncio
async def test_maintenance_keeps_all_when_no_transitive_data():
    # package.json fallback: cannot determine directness, keep everything
    graph = {"direct": {"express": "^4"}, "packages": {}}
    findings = [
        FindingNote(dep_name="express", severity="medium", description="stale", evidence=[]),
        FindingNote(dep_name="qs", severity="medium", description="stale", evidence=[]),
    ]
    with patch.object(
        MaintenanceAgent.__bases__[0], "run",
        AsyncMock(return_value=(_bundle(findings), [], 1)),
    ):
        bundle, _, _ = await MaintenanceAgent().run(_dispatch(), _prep(graph))

    assert {f.dep_name for f in bundle.findings} == {"express", "qs"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_maintenance_agent.py -v`
Expected: FAIL — `test_maintenance_drops_transitive_findings` fails because base `run` is used and `qs` is not dropped.

- [ ] **Step 3: Implement the `run` override**

In `src/main_graph/subgraphs/analysis/agents/maintenance_agent.py`, update the imports at the top:

```python
from __future__ import annotations

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.subgraphs.discovery.dependency_graph import is_direct
from src.main_graph.tools.external_api import (
    high_risk_packages,
    package_reputation,
    unmaintained_packages,
)
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult
```

Add a `run` method to the `MaintenanceAgent` class, after `_agent_tools` (keep `_agent_tools` as-is):

```python
    async def run(
        self,
        dispatch: AgentDispatch,
        prep: PrepResult,
        container: ContainerRunPort | None = None,
    ) -> tuple[EvidenceBundle, list[str], int]:
        """Maintenance is a quality-proxy analysis: "old"/"unmaintained" is only
        actionable for a dependency the user actually chose. A stale transitive
        under a healthy direct parent is the parent maintainer's concern, not an
        actionable risk here, so transitive findings are dropped deterministically
        (prompt guidance alone has leaked such findings before). When the graph
        has no transitive data (package.json fallback), directness is unknowable,
        so findings are kept rather than silently discarded.
        """
        bundle, tools_used, iterations = await super().run(dispatch, prep, container)
        if not prep.dependency_graph.get("direct"):
            return bundle, tools_used, iterations
        direct_only = [
            f for f in bundle.findings
            if is_direct(prep.dependency_graph, f.dep_name)
        ]
        if len(direct_only) != len(bundle.findings):
            bundle = bundle.model_copy(update={"findings": direct_only})
        return bundle, tools_used, iterations
```

Also add a line to the `system_prompt` (inside the existing `Rules on maintainer count:` area or as a new rule) making the scope explicit — additive to the deterministic filter. Add this rule right after the maintainer-count rules block:

```
        Scope:
        - Only assess DIRECT dependencies (declared in package.json). Do not
          create maintenance findings for transitive dependencies — their health
          is the direct parent's responsibility and is not directly actionable.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_maintenance_agent.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/main_graph/subgraphs/analysis/agents/maintenance_agent.py tests/unit/test_maintenance_agent.py && uv run mypy src/main_graph/subgraphs/analysis/agents/maintenance_agent.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/analysis/agents/maintenance_agent.py tests/unit/test_maintenance_agent.py
git commit -m "feat: scope maintenance findings to direct dependencies

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

### Task 3: Add `is_direct` and `direct_dependents` to `ReportFinding`

**Files:**
- Modify: `src/models/results.py` (`ReportFinding`, currently lines 94-105)
- Test: `tests/unit/test_result_models.py` (append)

**Interfaces:**
- Produces: `ReportFinding` with two new fields `is_direct: bool = True` and `direct_dependents: list[str] = []` (default factory list), backward compatible with existing constructions.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_result_models.py`:

```python
def test_report_finding_directness_defaults():
    f = ReportFinding(
        dep_name="express", severity="high", description="CVE",
        recommendation="upgrade",
    )
    assert f.is_direct is True
    assert f.direct_dependents == []


def test_report_finding_accepts_transitive_attribution():
    f = ReportFinding(
        dep_name="qs", severity="high", description="CVE",
        recommendation="update express",
        is_direct=False,
        direct_dependents=["express", "webpack"],
    )
    assert f.is_direct is False
    assert f.direct_dependents == ["express", "webpack"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_result_models.py -k directness -v` and `... -k transitive_attribution -v`
Expected: FAIL — `ReportFinding` has no `is_direct` field (validation/attribute error).

- [ ] **Step 3: Add the fields**

In `src/models/results.py`, update the `ReportFinding` class (add the two fields after `observation`):

```python
class ReportFinding(BaseModel):
    dep_name: str
    severity: str
    description: str
    recommendation: str
    alternatives: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    evidence: list = Field(default_factory=list)
    business_impact: str = ""
    blast_radius: BlastRadiusSummary | None = None
    trust: bool = True
    observation: str = ""
    # Directness attribution: dep_name is always the package where the issue
    # physically is; is_direct/direct_dependents record whether it is a declared
    # direct dependency and, if transitive, which direct deps pull it in. The
    # recommendation is always framed around the direct dependent(s).
    is_direct: bool = True
    direct_dependents: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_result_models.py -v`
Expected: PASS (all, including the two new ones).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/models/results.py tests/unit/test_result_models.py && uv run mypy src/models/results.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/models/results.py tests/unit/test_result_models.py
git commit -m "feat: add directness attribution fields to ReportFinding

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

### Task 4: Enricher stamps directness and anchors recommendation on direct deps

**Files:**
- Modify: `src/main_graph/subgraphs/report/agents/finding_enricher_agent.py`
- Test: `tests/unit/test_finding_enricher_agent.py` (append)

**Interfaces:**
- Consumes: `is_direct`, `direct_dependents` from Task 1; `ReportFinding.is_direct`, `ReportFinding.direct_dependents` from Task 3.
- Produces: `enrich_finding` unchanged signature (`(finding, prep, all_flagged_dep_names, container=None) -> tuple[ReportFinding, list[str]]`) but the returned draft has `is_direct`/`direct_dependents` set deterministically from `prep.dependency_graph`, and the system prompt carries a directness rule.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_finding_enricher_agent.py`:

```python
def _transitive_prep() -> PrepResult:
    return _prep(
        dependency_graph={
            "direct": {"express": "4.18.0"},
            "packages": {
                "express@4.18.0": {
                    "version": "4.18.0",
                    "dependencies": ["left-pad@1.0.0"],
                },
                "left-pad@1.0.0": {"version": "1.0.0", "dependencies": []},
            },
        }
    )


def _direct_prep() -> PrepResult:
    return _prep(
        dependency_graph={
            "direct": {"left-pad": "1.0.0"},
            "packages": {
                "left-pad@1.0.0": {"version": "1.0.0", "dependencies": []},
            },
        }
    )


@pytest.mark.asyncio
async def test_enrich_finding_stamps_transitive_attribution():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, _ = await finding_enricher_agent.enrich_finding(
            _finding(), _transitive_prep(), []
        )

    assert draft.is_direct is False
    assert draft.direct_dependents == ["express"]


@pytest.mark.asyncio
async def test_enrich_finding_stamps_direct_attribution():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_finalize()
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        draft, _ = await finding_enricher_agent.enrich_finding(
            _finding(), _direct_prep(), []
        )

    assert draft.is_direct is True
    assert draft.direct_dependents == []


@pytest.mark.asyncio
async def test_enrich_finding_transitive_prompt_names_direct_dependents():
    from src.main_graph.subgraphs.report.agents import finding_enricher_agent

    mock_llm = MagicMock()
    seen_system: dict = {}

    async def _ainvoke(messages):
        seen_system["content"] = messages[0]["content"]
        return _finalize()

    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=_ainvoke
    )
    critic = AsyncMock(return_value=_ok_verdict())

    with (
        patch.object(finding_enricher_agent, "_llm", mock_llm),
        patch.object(finding_enricher_agent, "critique_report_finding", critic),
    ):
        await finding_enricher_agent.enrich_finding(
            _finding(), _transitive_prep(), []
        )

    content = seen_system["content"]
    assert "transitive" in content.lower()
    assert "express" in content  # the direct dependent to anchor on
```

Note: `_prep` in this test file must accept `dependency_graph` via `**overrides` — it already does (`dependency_graph={}` default is overridable).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_finding_enricher_agent.py -k "attribution or names_direct" -v`
Expected: FAIL — `draft.is_direct`/`direct_dependents` not set from graph (default True/[] regardless), and prompt lacks directness text.

- [ ] **Step 3: Implement directness threading**

In `src/main_graph/subgraphs/report/agents/finding_enricher_agent.py`:

Add the import near the other `src.main_graph.subgraphs` imports:

```python
from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)
```

Add a `{directness_guidance}` placeholder to `_SYSTEM`. Replace the block from `Finding to enrich:` through `Available tools:` with:

```python
    Finding to enrich:
    - package: {dep_name}
    - severity: {severity}
    - description: {description}

    {directness_guidance}

    Available tools:
    {tool_descriptions}
```

And in the output-spec bullets, replace the `recommendation` bullet with:

```
    - recommendation: an action the user can actually take. The user's ONLY
      levers are on DIRECT dependencies (declared in package.json). Follow the
      directness guidance above: for a direct package, recommend upgrading or
      replacing it; for a transitive package, recommend updating the direct
      dependent(s) named above — never an action on the transitive itself.
```

Add a helper to build the guidance string (place near `_format_tools`):

```python
def _directness_guidance(dep_name: str, is_direct_dep: bool, dependents: list[str]) -> str:
    if is_direct_dep:
        return (
            f"'{dep_name}' is a DIRECT dependency (declared in package.json). "
            "Recommend the concrete fix the user applies directly: upgrade to a "
            "fixed version, or replace it with a safer package."
        )
    parents = ", ".join(dependents) if dependents else "an unknown direct dependency"
    return (
        f"'{dep_name}' is a TRANSITIVE dependency. It is NOT in package.json and "
        f"the user cannot upgrade, replace, pin, or override it directly. It is "
        f"pulled in by these direct dependencies: {parents}.\n"
        "Anchor everything actionable on the direct dependent(s) above:\n"
        f"- recommendation MUST target the direct dependent(s), e.g. \"update "
        f"<direct-dependent> to a version whose dependency tree no longer includes "
        f"{dep_name} (or resolves it to a fixed version)\". The finding description "
        "may already carry the exact fix path from the audit (e.g. \"Fix requires "
        "X@Y\"); prefer it when present.\n"
        "- If no direct-dependent update resolves it (description says no fix is "
        f"available), say so honestly, then suggest replacing the direct "
        f"dependent(s) or accepting the risk — never patching {dep_name}.\n"
        f"- Do NOT suggest replacing, forking, or adding overrides/resolutions for "
        f"{dep_name}, and do NOT put {dep_name} in alternatives.\n"
        "- alternatives: leave empty unless proposing a replacement for a direct "
        "dependent."
    )
```

In `enrich_finding`, compute directness once before the loop (after `excluded = ...`):

```python
    finding_is_direct = is_direct(prep.dependency_graph, finding.dep_name)
    dependents = (
        [] if finding_is_direct
        else direct_dependents(prep.dependency_graph, finding.dep_name)
    )
    guidance = _directness_guidance(finding.dep_name, finding_is_direct, dependents)
```

Add `directness_guidance=guidance` to the `_SYSTEM.format(...)` call inside the loop:

```python
        system = _SYSTEM.format(
            dep_name=finding.dep_name,
            severity=finding.severity,
            description=finding.description,
            tool_descriptions=_format_tools(tool_map),
            excluded_alternatives=excluded,
            max_iter=_MAX_ITERATIONS,
            directness_guidance=guidance,
        )
```

Stamp the fields deterministically on the returned draft. Replace the final `return draft, [tr.tool for tr in tool_results]` (after the `if draft is None:` fallback block) with:

```python
    draft.is_direct = finding_is_direct
    draft.direct_dependents = dependents
    return draft, [tr.tool for tr in tool_results]
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/unit/test_finding_enricher_agent.py -v`
Expected: PASS (all, including the three new tests and the unchanged existing ones — the existing `_prep()` uses `dependency_graph={}`, so `is_direct` is False and `direct_dependents` is `[]`, which does not affect their assertions).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src/main_graph/subgraphs/report/agents/finding_enricher_agent.py tests/unit/test_finding_enricher_agent.py && uv run mypy src/main_graph/subgraphs/report/agents/finding_enricher_agent.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/subgraphs/report/agents/finding_enricher_agent.py tests/unit/test_finding_enricher_agent.py
git commit -m "feat: anchor enriched-finding recommendations on direct dependencies

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

### Task 5: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `uv run pytest`
Expected: all tests pass (no regressions in `test_report_subgraph`, `test_analysis_subgraph`, `test_audit_parser`, etc.).

- [ ] **Step 2: Lint and type-check the whole backend**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 3: Sanity-check the behavior end-to-end (optional but recommended)**

If a MongoDB testcontainer / Docker is available, run the report subgraph integration test specifically to confirm enriched findings carry directness:

Run: `uv run pytest tests/subgraphs/test_report_subgraph.py -v`
Expected: PASS.

- [ ] **Step 4: Final commit if any residual fixes were needed**

```bash
git add -A
git commit -m "test: verify direct-anchored findings across backend suite

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BJgnSKkffGfLQHdHB5FE64"
```

---

## Self-Review Notes

- **Spec coverage:** D1 → Task 2; D2/D3 → Tasks 3+4; D4 (fan-out) → `direct_dependents` returns all parents (Task 1) + prompt "direct dependent(s)" plural (Task 4); D5 (fix path) → prompt references the audit "Fix requires X@Y" text already in the description (Task 4), no new plumbing; D6 (offline) → helpers read `prep.dependency_graph` only (Tasks 1,4); D7 (deterministic) → maintenance filter + field stamping in code (Tasks 2,4).
- **`dep_name` preserved:** no task overwrites `dep_name`; identity/dedup unaffected.
- **Type consistency:** `is_direct(graph, name) -> bool` and `direct_dependents(graph, name) -> list[str]` used identically in Tasks 2 and 4; `ReportFinding.is_direct: bool` / `direct_dependents: list[str]` defined in Task 3 and set in Task 4.
- **Backward compatibility:** new `ReportFinding` fields have defaults, so existing constructions (fallback finding, tests) remain valid.
