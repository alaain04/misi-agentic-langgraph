# Concern-Aware Coverage Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the analysis deep agent from forcing per-package coverage (e.g. dispatching `web_research_agent`) of every direct dependency when a whole-tree scan that already succeeded (Trivy vulnerability/license scan) fully answers the user's concern.

**Architecture:** Add a new LLM-judgment function `whole_tree_scan_satisfies_concern` to `coverage.py`; have `coverage_gate` (in `deepagent/nodes.py`) call it only when the set of *successfully completed* whole-tree agents grows since the last check (cached in `AnalysisState`), and short-circuit `missing_deps` to `[]` when the judge says the concern is fully addressed — which also prevents `backstop_dispatch` from ever firing, since it's only reached when `missing_deps` is non-empty.

**Tech Stack:** Python 3.12, LangGraph, Pydantic, pytest (`asyncio_mode = "auto"`), MongoDB via `ResultDAO`.

## Global Constraints

- LLM judge model: `Model.GPT_5_4_MINI` via `src.utils.llm.get_llm`, matching every other structured-output judge in this codebase (`critique.py`, `finding_enricher`, etc.).
- On any judge failure (exception, empty concern, no whole-tree agents ran) the function must return `False` — the conservative default already used throughout this codebase (a spurious `False` only costs extra coverage, never missed coverage).
- A whole-tree agent counts as "successfully run" only if its `EvidenceBundle.confidence > 0.5` (`vulnerability_agent` sets `0.3` on a Trivy error, `1.0` on success; `license_agent` always sets `1.0`). A missing/unfetchable bundle does not count as successful.
- No changes to `backstop.py` — forcing `missing_deps = []` upstream already prevents it from ever being reached for a fully-addressed concern.
- No new Mongo persistence — the two new `AnalysisState` fields are transient per-run state, not written to `AgentCallRecord`/`EvidenceBundle`.
- Test commands use `uv run pytest <path> -v` (per `apps/backend/Makefile`; `asyncio_mode = "auto"` means no `@pytest.mark.asyncio` decorator is strictly required, but this codebase still adds it on existing async tests — follow that existing convention when touching those files).

---

### Task 1: Add the `whole_tree_scan_satisfies_concern` judge to `coverage.py`

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/coverage.py`
- Test: `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage.py`

**Interfaces:**
- Produces: `async def whole_tree_scan_satisfies_concern(concern: str, ran_whole_tree_agents: list[str]) -> bool`, module-private `class _CoverageJudgment(BaseModel)`, and a module-level `_llm` (patchable in tests as `src.main_graph.subgraphs.analysis.deepagent.coverage._llm`, matching the existing pattern in `src/main_graph/subgraphs/analysis/agents/critique.py`).
- Consumes: `get_agent_descriptions()` from `src.main_graph.subgraphs.analysis.agents.registry` (existing), `Model.GPT_5_4_MINI` / `get_llm` from `src.utils.llm` (existing).

- [ ] **Step 1: Write the failing tests**

Append to `apps/backend/tests/unit/subgraphs/analysis/deepagent/test_coverage.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_whole_tree_scan_satisfies_concern_false_on_empty_concern():
    from src.main_graph.subgraphs.analysis.deepagent.coverage import (
        whole_tree_scan_satisfies_concern,
    )

    # No LLM call should happen for an empty concern -- if _llm were touched
    # without being patched, this would raise trying to reach the network.
    result = await whole_tree_scan_satisfies_concern("", ["vulnerability_agent"])
    assert result is False


@pytest.mark.asyncio
async def test_whole_tree_scan_satisfies_concern_false_when_nothing_ran():
    from src.main_graph.subgraphs.analysis.deepagent.coverage import (
        whole_tree_scan_satisfies_concern,
    )

    result = await whole_tree_scan_satisfies_concern("analyze vulnerable dependencies", [])
    assert result is False


@pytest.mark.asyncio
async def test_whole_tree_scan_satisfies_concern_true_when_llm_says_covered():
    from src.main_graph.subgraphs.analysis.deepagent.coverage import (
        _CoverageJudgment,
        whole_tree_scan_satisfies_concern,
    )

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_CoverageJudgment(
            fully_addressed=True, reason="Trivy already covers known CVEs"
        )
    )
    with patch(
        "src.main_graph.subgraphs.analysis.deepagent.coverage._llm", mock_llm
    ):
        result = await whole_tree_scan_satisfies_concern(
            "analyze vulnerable dependencies", ["vulnerability_agent"]
        )
    assert result is True
    mock_llm.with_structured_output.assert_called_once()


@pytest.mark.asyncio
async def test_whole_tree_scan_satisfies_concern_false_when_llm_says_not_covered():
    from src.main_graph.subgraphs.analysis.deepagent.coverage import (
        _CoverageJudgment,
        whole_tree_scan_satisfies_concern,
    )

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=_CoverageJudgment(
            fully_addressed=False, reason="concern also asks about maintenance"
        )
    )
    with patch(
        "src.main_graph.subgraphs.analysis.deepagent.coverage._llm", mock_llm
    ):
        result = await whole_tree_scan_satisfies_concern(
            "check for vulnerabilities and unmaintained packages",
            ["vulnerability_agent"],
        )
    assert result is False


@pytest.mark.asyncio
async def test_whole_tree_scan_satisfies_concern_false_on_llm_exception():
    from src.main_graph.subgraphs.analysis.deepagent.coverage import (
        whole_tree_scan_satisfies_concern,
    )

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    with patch(
        "src.main_graph.subgraphs.analysis.deepagent.coverage._llm", mock_llm
    ):
        result = await whole_tree_scan_satisfies_concern(
            "analyze vulnerable dependencies", ["vulnerability_agent"]
        )
    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_coverage.py -v`
Expected: the 5 new tests FAIL with `ImportError: cannot import name 'whole_tree_scan_satisfies_concern'` (or `'_CoverageJudgment'`) — the 6 pre-existing tests in this file still PASS.

- [ ] **Step 3: Implement `whole_tree_scan_satisfies_concern` in `coverage.py`**

Replace the full contents of `apps/backend/src/main_graph/subgraphs/analysis/deepagent/coverage.py` with:

```python
"""Deterministic coverage guarantee for the analysis deep agent (spec D5, D8).

The deep agent decides HOW to investigate; whether every direct dependency
got looked at by a package-scoped agent is never left to its judgment --
UNLESS a whole-tree scan that already ran fully addresses the concern (see
whole_tree_scan_satisfies_concern below), in which case per-package coverage
of the rest would add nothing.
"""

from __future__ import annotations

import logging
import textwrap
from typing import cast

from pydantic import BaseModel, Field

from src.main_graph.subgraphs.analysis.agents.registry import (
    REGISTRY,
    get_agent_descriptions,
)
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

WHOLE_TREE_AGENT_TYPES: set[str] = {"vulnerability_agent", "license_agent"}
"""Agents that scan the entire dependency tree in one run -- a second
dispatch adds no coverage and is capped to one run/job (D8), and they never
count toward direct-dependency coverage in compute_missing_direct_deps."""

PACKAGE_SCOPED_AGENT_TYPES: set[str] = set(REGISTRY) - WHOLE_TREE_AGENT_TYPES


def compute_missing_direct_deps(
    agent_calls: list[dict], direct_deps: list[str]
) -> list[str]:
    """Direct deps with no package-scoped AgentCallRecord covering them.

    agent_calls: list of AgentCallRecord.model_dump()-shaped dicts
    (agent_type, packages_to_focus, ...). Order of the returned list follows
    direct_deps, not agent_calls.
    """
    covered: set[str] = set()
    for call in agent_calls:
        if call.get("agent_type") in PACKAGE_SCOPED_AGENT_TYPES:
            covered.update(call.get("packages_to_focus") or [])
    return [dep for dep in direct_deps if dep not in covered]


_llm = get_llm(Model.GPT_5_4_MINI)

_COVERAGE_JUDGE_SYSTEM = textwrap.dedent("""\
    You decide whether a dependency-risk investigation has already fully
    addressed the user's concern.

    Some specialist scanners examine the ENTIRE dependency tree in a single
    run (every direct and transitive dependency at once). These whole-tree
    scanners have already completed SUCCESSFULLY for this job:
    {roster}

    Decide whether those whole-tree scans ALONE fully address the user's
    concern. Answer true only when investigating the remaining dependencies
    one-by-one would add nothing the concern asks for. If the concern also
    touches anything those scanners do not cover (e.g. maintenance/outdatedness,
    supply-chain/typosquatting, or open-ended web research), answer false.
    """).strip()


class _CoverageJudgment(BaseModel):
    fully_addressed: bool = Field(
        description=(
            "True if the whole-tree scans already run fully address the "
            "user's concern, so per-package investigation of the remaining "
            "dependencies would add nothing."
        )
    )
    reason: str = Field(description="One short sentence justifying the decision.")


async def whole_tree_scan_satisfies_concern(
    concern: str, ran_whole_tree_agents: list[str]
) -> bool:
    """LLM judgment: do the whole-tree scans that already completed
    successfully fully address `concern`, making per-package coverage of the
    remaining direct deps unnecessary?

    Returns False (keep requiring per-package coverage) when no whole-tree
    scan ran, the concern is empty, or the model call fails -- the
    conservative choice, since a spurious False only costs extra coverage,
    never missed coverage.
    """
    if not concern.strip() or not ran_whole_tree_agents:
        return False
    descriptions = get_agent_descriptions()
    roster = "\n".join(
        f"- {a}: {descriptions.get(a, '')}" for a in sorted(ran_whole_tree_agents)
    )
    system = _COVERAGE_JUDGE_SYSTEM.format(roster=roster)
    structured = _llm.with_structured_output(
        _CoverageJudgment, method="function_calling"
    )
    try:
        judgment = cast(
            _CoverageJudgment,
            await structured.ainvoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"User concern: {concern}"},
                ]
            ),
        )
    except Exception:
        logger.warning(
            "whole_tree_scan_satisfies_concern: LLM judgment failed; "
            "falling back to per-package coverage",
            exc_info=True,
        )
        return False
    return judgment.fully_addressed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && uv run pytest tests/unit/subgraphs/analysis/deepagent/test_coverage.py -v`
Expected: all 11 tests PASS (6 pre-existing + 5 new).

- [ ] **Step 5: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/analysis/deepagent/coverage.py tests/unit/subgraphs/analysis/deepagent/test_coverage.py
git commit -m "feat: add whole_tree_scan_satisfies_concern coverage judge"
```

---

### Task 2: Wire the judge into `coverage_gate`, add cached state, and prove the fix end-to-end

**Files:**
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/state.py`
- Modify: `apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py`
- Modify: `apps/backend/tests/subgraphs/test_analysis_subgraph.py`

**Interfaces:**
- Consumes (from Task 1 + existing code): `whole_tree_scan_satisfies_concern(concern: str, ran_whole_tree_agents: list[str]) -> bool` and `WHOLE_TREE_AGENT_TYPES: set[str]` from `coverage.py`; `compute_missing_direct_deps` (unchanged signature); `ResultDAO.get_bundles(ids: list[str]) -> list[EvidenceBundle]` (existing, `src/db/result_dao.py:31`).
- Produces: `coverage_gate` now also returns `whole_tree_checked_roster: list[str]` and `whole_tree_satisfies_concern: bool` in its result dict, alongside the existing `missing_deps`/`correction_rounds`.

- [ ] **Step 1: Add the two new cached fields to `AnalysisState`**

In `apps/backend/src/main_graph/subgraphs/analysis/state.py`, add two lines inside the `AnalysisState` class, directly after `correction_rounds`:

```python
class AnalysisState(TypedDict):
    # From MainState (matched by key name)
    job_id: str
    concern: str
    prep_result_id: str

    # Internal — deep agent run + coverage loop
    # deepagent_state: last full state returned by deep_agent.ainvoke()
    deepagent_state: NotRequired[dict]
    missing_deps: NotRequired[list[str]]
    correction_rounds: NotRequired[int]
    whole_tree_checked_roster: NotRequired[list[str]]
    whole_tree_satisfies_concern: NotRequired[bool]
    bundle_ids: Annotated[list[str], operator.add]
    agent_calls: Annotated[
        list[dict], operator.add
    ]  # AgentCallRecord.model_dump() per domain_agent call

    # Output (written back to MainState)
    analysis_result_id: NotRequired[str]
```

- [ ] **Step 2: Modify the existing two-correction-round test to keep its intent, and add a new regression test — write these FIRST (they must fail against today's code)**

In `apps/backend/tests/subgraphs/test_analysis_subgraph.py`, modify
`test_analysis_accumulates_bundles_across_two_correction_rounds`'s `with (...)`
block to also patch the new judge, forcing the concern to still require a
second correction round (this test's concern, "dependency health", is
broader than pure vulnerability scanning, so in production the real judge
would say `False` here too — this patch just makes that deterministic
instead of a live LLM call):

```python
    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(side_effect=_extract_as),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.trivy_vuln_scan",
            AsyncMock(return_value=_TRIVY_FIXTURE),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.base_agent._llm",
            _fake_base_llm(maintenance_decision),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.whole_tree_scan_satisfies_concern",
            AsyncMock(return_value=False),
        ),
    ):
```

Then append this new test to the same file (it reproduces the exact shape of
job `6a6db91f414c989f5ecd71a9`: a pure-vulnerability concern, one successful
Trivy scan, judge says fully covered):

```python
@pytest.mark.asyncio
async def test_coverage_gate_skips_per_package_coverage_when_whole_tree_scan_satisfies_concern(
    subgraph_config, result_dao
):
    """Regression test for the redundant web_research_agent dispatch found in
    job 6a6db91f414c989f5ecd71a9: concern is purely about known
    vulnerabilities, vulnerability_agent's Trivy scan succeeds, and the
    coverage judge says that fully addresses the concern. coverage_gate must
    then short-circuit missing_deps to [] -- no correction-round loop-back,
    no backstop_dispatch, no web_research_agent/maintenance_agent/
    supply_chain_agent ever dispatched."""
    job_id = f"anal-{uuid.uuid4().hex[:8]}"
    prep = _seed_prep(job_id)
    await result_dao.save_prep(prep)

    fake_deep_agent = _build_fake_deep_agent(
        [
            _task_call(
                "Scan the whole dependency tree for known CVEs.",
                "vulnerability_agent",
                "call_vuln",
            ),
            AIMessage(content="Sufficient evidence collected, finalizing."),
        ]
    )

    with (
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(side_effect=_extract_as),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.vulnerability_agent.trivy_vuln_scan",
            AsyncMock(return_value=_TRIVY_FIXTURE),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.whole_tree_scan_satisfies_concern",
            AsyncMock(return_value=True),
        ),
    ):
        graph = build_analysis_subgraph()
        result = await graph.ainvoke(
            {
                "job_id": job_id,
                "concern": "analyze vulnerable dependencies",
                "prep_result_id": prep.id,
                "bundle_ids": [],
                "agent_calls": [],
            },
            config=subgraph_config,
        )

    assert result.get("analysis_result_id")
    analysis = await result_dao.get_analysis(result["analysis_result_id"])
    assert len(analysis.evidence_bundle_ids) == 1
    assert len(analysis.findings) == 1
    assert analysis.findings[0].dep_name == "lodash"

    job_repo = subgraph_config["configurable"]["job_repo"]
    call = job_repo.update_artifact_data.await_args
    agent_calls = call.args[2]["agent_calls"]
    # Only vulnerability_agent ran -- the coverage judge prevented a forced
    # dispatch of web_research_agent (or any other package-scoped agent) for
    # a concern the Trivy scan already fully answered.
    assert len(agent_calls) == 1
    assert agent_calls[0]["agent_type"] == "vulnerability_agent"
```

- [ ] **Step 3: Run the subgraph tests to verify the new/modified tests fail**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_analysis_subgraph.py -v`
Expected:
- `test_analysis_accumulates_bundles_across_two_correction_rounds` FAILS with
  `AttributeError: <module ...nodes> does not have the attribute
  'whole_tree_scan_satisfies_concern'` (nodes.py doesn't import it yet).
- `test_coverage_gate_skips_per_package_coverage_when_whole_tree_scan_satisfies_concern`
  FAILS the same way.
- The other three tests in the file still PASS.

- [ ] **Step 4: Wire the judge into `coverage_gate`**

In `apps/backend/src/main_graph/subgraphs/analysis/deepagent/nodes.py`, update the coverage import (currently only imports `compute_missing_direct_deps`):

```python
from src.main_graph.subgraphs.analysis.deepagent.coverage import (
    WHOLE_TREE_AGENT_TYPES,
    compute_missing_direct_deps,
    whole_tree_scan_satisfies_concern,
)
```

Then replace the `coverage_gate` function body:

```python
async def coverage_gate(state: AnalysisState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    dao = svc["result_dao"]
    prep = await dao.get_prep(state["prep_result_id"])
    agent_calls = state.get("agent_calls") or []

    whole_tree_calls = [
        c for c in agent_calls if c.get("agent_type") in WHOLE_TREE_AGENT_TYPES
    ]
    bundle_id_by_type = {c["agent_type"]: c["bundle_id"] for c in whole_tree_calls}
    bundles = (
        await dao.get_bundles(list(bundle_id_by_type.values()))
        if bundle_id_by_type
        else []
    )
    bundle_by_id = {b.id: b for b in bundles}
    successful_roster = sorted(
        agent_type
        for agent_type, bundle_id in bundle_id_by_type.items()
        if (bundle := bundle_by_id.get(bundle_id)) is not None
        and bundle.confidence > 0.5
    )

    checked_roster = state.get("whole_tree_checked_roster")
    if successful_roster and successful_roster != checked_roster:
        satisfies = await whole_tree_scan_satisfies_concern(
            state["concern"], successful_roster
        )
    else:
        satisfies = state.get("whole_tree_satisfies_concern", False)

    if satisfies:
        missing: list[str] = []
    else:
        direct_deps = list(prep.dependency_graph.get("direct", {}).keys())
        missing = compute_missing_direct_deps(agent_calls, direct_deps)

    return {
        "missing_deps": missing,
        "correction_rounds": (state.get("correction_rounds") or 0) + 1,
        "whole_tree_checked_roster": successful_roster,
        "whole_tree_satisfies_concern": satisfies,
    }
```

- [ ] **Step 5: Run the subgraph tests to verify they now pass**

Run: `cd apps/backend && uv run pytest tests/subgraphs/test_analysis_subgraph.py -v`
Expected: all 5 tests PASS (4 pre-existing + 1 new).

- [ ] **Step 6: Run the full backend test suite to check for regressions**

Run: `cd apps/backend && uv run pytest -v`
Expected: PASS (no regressions elsewhere — `coverage_gate` is only called from the analysis subgraph wiring exercised above).

- [ ] **Step 7: Commit**

```bash
cd apps/backend
git add src/main_graph/subgraphs/analysis/state.py src/main_graph/subgraphs/analysis/deepagent/nodes.py tests/subgraphs/test_analysis_subgraph.py
git commit -m "feat: skip forced per-package coverage when a whole-tree scan already satisfies the concern"
```
