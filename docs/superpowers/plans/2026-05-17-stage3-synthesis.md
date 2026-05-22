# Stage 3 Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three stub nodes (`risk_ranker`, `risk_score`, `recommendation`) with fully agentic implementations that use LLM + tools to analyze Stage 1/2 data, produce final risk scores, and find alternatives.

**Architecture:** Each node uses `create_agent` from `langchain.agents` with `@tool`-decorated closures over state data. `risk_ranker`'s `save_ranking` tool uses `return_direct=True` (called once with all rankings). `risk_score` and `recommendation` save tools do NOT use `return_direct` — they're called once per dep and the agent exits naturally when it stops making tool calls. npm/GitHub HTTP functions live in a separate `recommendation_tools.py` module. `risk_ranker` keeps its existing router (`risk_ranker_router`) unchanged.

**Tech Stack:** Python 3.12, LangGraph, langchain (`create_agent`, `@tool`), httpx (async HTTP), pytest + pytest-asyncio (`asyncio_mode = "auto"`), `uv run pytest`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/main_graph/nodes/risk_ranker.py` | Modify | Agentic node that cross-analyzes Stage 1 signals + selection logic; keep router |
| `src/main_graph/nodes/risk_score.py` | Modify | Agentic node that scores all deps using Stage 1 + 2 signals |
| `src/main_graph/nodes/recommendation_tools.py` | Create | Pure async HTTP functions for npm/GitHub lookups |
| `src/main_graph/nodes/recommendation.py` | Modify | Agentic node that finds alternatives for high-risk deps |
| `tests/unit/nodes/test_risk_ranker.py` | Create | Unit tests for risk_ranker node |
| `tests/unit/nodes/test_risk_score.py` | Create | Unit tests for risk_score node |
| `tests/unit/nodes/test_recommendation_tools.py` | Create | Unit tests for npm/GitHub HTTP functions |
| `tests/unit/nodes/test_recommendation.py` | Create | Unit tests for recommendation node |

### Codebase patterns to follow

- `create_agent(model=_llm, tools=tools)` — no `response_format` when agent uses save-tool pattern
- `await agent.ainvoke({"messages": [SystemMessage(...), HumanMessage(...)]}, config={"recursion_limit": 40})`
- Tools are `@tool`-decorated sync or async closures over state variables
- `@tool(return_direct=True)` on `save_ranking` ONLY (called once with full list); per-dep saves (`save_risk_score`, `save_recommendation`) do NOT use `return_direct` — agent exits naturally when it stops calling tools
- `subgraph_results: list[dict]` in state has entries `{"subgraph": name, "dep_name": dep_name, "result_id": result_id}`
  - SBOM-level subgraphs (vulnerabilities, license_compliance): `dep_name` is `None`
  - Per-dep subgraphs (registry, repo, runtime, impact): `dep_name` is the package name
- `SUBGRAPH_DAOS` dict from `src.main_graph.subgraphs.ingestion_subgraphs` maps subgraph name → DAO
- DAO `.get(result_id) -> dict | None` — returns the stored document as a plain dict

### DAO document shapes

- `vulnerabilities_dao.get(id)` → `{"records": [{"name": str, "version": str, "findings": [...], "risk_level": str}], "total_findings": int}`
- `license_compliance_dao.get(id)` → `{"records": [{"name": str, "license": str, "is_compliant": bool, "risk_level": str}]}`
- `registry_dao.get(id)` → `{"dep_name": str, "last_publish": str|None, "weekly_downloads": int|None, "is_deprecated": bool, "maintainers_count": int|None}`
- `repo_dao.get(id)` → `{"repositories": [{"url": str, "stars": int|None, "open_issues": int|None}]}`
- `runtime_dao.get(id)` → `{"test_results": {"passed": int, "failed": int, "errors": [str]}, "lint_results": {"errors": int, "warnings": int}}`
- `impact_dao.get(id)` → `{"dep_name": str, "usage_count": int, "affected_files": [...], "transitive_dependents": int, "blast_radius_summary": str}`

---

## Task 1: risk_ranker agentic node

**Files:**
- Modify: `src/main_graph/nodes/risk_ranker.py`
- Create: `tests/unit/nodes/test_risk_ranker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/nodes/test_risk_ranker.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.risk_ranker import _select_high_risk, risk_ranker, risk_ranker_router


def _base_state():
    return {
        "plan": {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None},
        "sbom_cyclonedx": {
            "components": [
                {"name": "express", "version": "4.18.2"},
                {"name": "lodash", "version": "4.17.21"},
                {"name": "cookie", "version": "0.5.0"},
            ]
        },
        "subgraph_results": [],
        "execution_stages": [],
        "messages": [],
    }


# ── _select_high_risk ────────────────────────────────────────────────────────

def test_select_high_risk_returns_top3():
    rankings = [
        {"dep_name": "A", "preliminary_score": 9.0, "risk_signals": []},
        {"dep_name": "B", "preliminary_score": 8.0, "risk_signals": []},
        {"dep_name": "C", "preliminary_score": 7.0, "risk_signals": []},
        {"dep_name": "D", "preliminary_score": 2.0, "risk_signals": []},
    ]
    result = _select_high_risk(rankings)
    assert set(result) == {"A", "B", "C"}
    assert "D" not in result


def test_select_high_risk_includes_high_score_beyond_top3():
    rankings = [
        {"dep_name": "A", "preliminary_score": 9.0, "risk_signals": []},
        {"dep_name": "B", "preliminary_score": 8.5, "risk_signals": []},
        {"dep_name": "C", "preliminary_score": 8.0, "risk_signals": []},
        {"dep_name": "D", "preliminary_score": 7.5, "risk_signals": []},  # score >= 7.0 but not top-3
        {"dep_name": "E", "preliminary_score": 1.0, "risk_signals": []},
    ]
    result = _select_high_risk(rankings)
    assert "D" in result  # score >= 7.0
    assert "E" not in result


def test_select_high_risk_empty_returns_empty():
    assert _select_high_risk([]) == []


# ── risk_ranker node ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_ranker_calls_agent_and_returns_fallback_on_empty_rankings():
    """When mock agent doesn't call save_ranking, fallback stub data is returned."""
    with patch(
        "src.main_graph.nodes.risk_ranker.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_ranker(_base_state())

    mock_factory.assert_called_once()
    assert result["risk_ranker_done"] is True
    assert len(result["risk_rankings"]) == 3  # one per SBOM component
    assert result["risk_rankings"][0]["dep_name"] in {"express", "lodash", "cookie"}
    assert result["risk_rankings"][0]["preliminary_score"] == 5.0
    # high_risk_deps = top-3 of 3 deps
    assert len(result["high_risk_deps"]) == 3


@pytest.mark.asyncio
async def test_risk_ranker_falls_back_on_agent_exception():
    with patch(
        "src.main_graph.nodes.risk_ranker.create_agent"
    ) as mock_factory:
        mock_factory.side_effect = RuntimeError("LLM unavailable")

        result = await risk_ranker(_base_state())

    assert result["risk_ranker_done"] is True
    assert len(result["risk_rankings"]) == 3


@pytest.mark.asyncio
async def test_risk_ranker_extends_execution_stages_for_impact():
    state = _base_state()
    state["plan"] = {"subgraphs": ["vulnerabilities", "impact"], "dep_filter": None}

    with patch("src.main_graph.nodes.risk_ranker.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_ranker(state)

    # high_risk_deps is non-empty and "impact" is in subgraphs → new stage added
    assert len(result["execution_stages"]) == 1
    stage2 = result["execution_stages"][0]
    assert all(entry["subgraph"] == "impact" for entry in stage2)


@pytest.mark.asyncio
async def test_risk_ranker_no_impact_in_plan_skips_stage_extension():
    state = _base_state()
    state["plan"] = {"subgraphs": ["vulnerabilities"], "dep_filter": None}

    with patch("src.main_graph.nodes.risk_ranker.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_ranker(state)

    assert result["execution_stages"] == []


def test_risk_ranker_router_routes_to_execution_planner_when_impact_and_high_risk():
    state = {
        "plan": {"subgraphs": ["impact"]},
        "high_risk_deps": ["express"],
    }
    assert risk_ranker_router(state) == "execution_planner"


def test_risk_ranker_router_routes_to_risk_score_when_no_impact():
    state = {
        "plan": {"subgraphs": ["vulnerabilities"]},
        "high_risk_deps": [],
    }
    assert risk_ranker_router(state) == "risk_score"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_risk_ranker.py -v 2>&1 | head -30
```

Expected: ImportError or AssertionError — `_select_high_risk` not defined yet.

- [ ] **Step 3: Write the implementation**

Replace `src/main_graph/nodes/risk_ranker.py` entirely:

```python
"""risk_ranker — agentic node and stage router."""

from __future__ import annotations

import json
import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.main_graph.constants import EXECUTION_PLANNER, RISK_SCORE
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_DAOS
from src.utils.llm import Model, get_llm

_log = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_PROMPT = """\
You are a risk ranking agent. Analyze all Stage 1 signals for every dependency \
in scope and produce a ranked list ordered from highest to lowest risk.

Steps:
1. Call list_analyzed_deps to get all deps in scope.
2. For each dep, call get_vulnerabilities, get_registry_data, get_repo_data, \
   get_runtime_data, and get_license_compliance to retrieve signals.
3. Reason about cross-domain risk: a deprecated package with CVEs is higher \
   risk than one with CVEs alone; unmaintained + failing tests = critical.
4. Assign a preliminary_score (0–10) to each dep.
5. For each dep, include risk_signals: list of strings describing the risks \
   found (e.g. "CVE-2023-1234 CRITICAL", "is_deprecated=true", "3 test failures").
6. Call save_ranking with the full ranked list. Include ALL deps in scope.

save_ranking input: list of objects with keys:
  dep_name (str), preliminary_score (float 0–10),
  risk_signals (list[str]), rationale (str)
"""


async def risk_ranker(state: MainState) -> dict:
    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else []
    )
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )
    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    subgraph_results: list[dict] = state.get("subgraph_results") or []

    def _find_result_id(subgraph: str, dep_name: str | None = None) -> str | None:
        for r in subgraph_results:
            if r.get("subgraph") == subgraph and r.get("dep_name") == dep_name:
                return r.get("result_id")
        return None

    _rankings: list[dict] = []

    @tool
    def list_analyzed_deps() -> str:
        """Return all dependency names in scope for this analysis."""
        return json.dumps(dep_scope)

    @tool
    async def get_vulnerabilities(dep_name: str) -> str:
        """Return vulnerability findings for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("vulnerabilities")
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["vulnerabilities"].get(result_id)
        if not doc:
            return json.dumps({})
        records = [r for r in doc.get("records", []) if r.get("name") == dep_name]
        return json.dumps({"records": records})

    @tool
    async def get_license_compliance(dep_name: str) -> str:
        """Return license compliance data for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("license_compliance")
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["license_compliance"].get(result_id)
        if not doc:
            return json.dumps({})
        records = [r for r in doc.get("records", []) if r.get("name") == dep_name]
        return json.dumps({"records": records})

    @tool
    async def get_registry_data(dep_name: str) -> str:
        """Return npm registry health data for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("registry", dep_name)
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["registry"].get(result_id)
        return json.dumps(doc or {})

    @tool
    async def get_repo_data(dep_name: str) -> str:
        """Return GitHub repository signals for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("repo", dep_name)
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["repo"].get(result_id)
        return json.dumps(doc or {})

    @tool
    async def get_runtime_data(dep_name: str) -> str:
        """Return runtime test/lint results for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("runtime", dep_name)
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["runtime"].get(result_id)
        return json.dumps(doc or {})

    @tool(return_direct=True)
    def save_ranking(rankings: list[dict]) -> str:
        """Save the final risk ranking for all deps.

        rankings: list of {dep_name, preliminary_score, risk_signals, rationale}
        """
        _rankings.extend(rankings)
        return "ok"

    tools = [
        list_analyzed_deps,
        get_vulnerabilities,
        get_license_compliance,
        get_registry_data,
        get_repo_data,
        get_runtime_data,
        save_ranking,
    ]

    try:
        agent = create_agent(model=_llm, tools=tools)
        await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=f"Rank the risk of these deps: {dep_scope}"
                    ),
                ]
            },
            config={"recursion_limit": 40},
        )
    except Exception:
        _log.exception("risk_ranker: agent failed")
        _rankings.clear()

    if not _rankings:
        _rankings.extend(
            [
                {
                    "dep_name": dep,
                    "preliminary_score": 5.0,
                    "risk_signals": [],
                    "rationale": "analysis unavailable",
                }
                for dep in dep_scope
            ]
        )

    high_risk_deps = _select_high_risk(_rankings)
    existing_stages = state.get("execution_stages") or []

    if "impact" in subgraphs and high_risk_deps:
        stage2 = [{"subgraph": "impact", "dep_name": dep} for dep in high_risk_deps]
        new_stages = existing_stages + [stage2]
    else:
        new_stages = list(existing_stages)

    _log.info(
        "risk_ranker: ranked %d deps, high_risk=%s", len(_rankings), high_risk_deps
    )
    return {
        "execution_stages": new_stages,
        "risk_rankings": _rankings,
        "high_risk_deps": high_risk_deps,
        "risk_ranker_done": True,
    }


def _select_high_risk(rankings: list[dict]) -> list[str]:
    """Select high-risk deps: top-3 by score plus any with score >= 7.0."""
    if not rankings:
        return []
    sorted_by_score = sorted(
        rankings, key=lambda r: r.get("preliminary_score", 0), reverse=True
    )
    top3 = {r["dep_name"] for r in sorted_by_score[:3]}
    high_score = {r["dep_name"] for r in rankings if r.get("preliminary_score", 0) >= 7.0}
    return list(top3 | high_score)


def risk_ranker_router(state: MainState) -> str:
    """Route after risk_ranker: to Stage 2 dispatch or directly to risk_score."""
    plan_obj = state.get("plan") or {}
    subgraphs: list[str] = (
        plan_obj.get("subgraphs", []) if isinstance(plan_obj, dict) else []
    )
    if "impact" in subgraphs and state.get("high_risk_deps"):
        return EXECUTION_PLANNER
    return RISK_SCORE
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_risk_ranker.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/risk_ranker.py tests/unit/nodes/test_risk_ranker.py
git commit -m "feat(risk_ranker): replace stub with agentic implementation"
```

---

## Task 2: risk_score agentic node

**Files:**
- Modify: `src/main_graph/nodes/risk_score.py`
- Create: `tests/unit/nodes/test_risk_score.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/nodes/test_risk_score.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.risk_score import risk_score


def _base_state():
    return {
        "plan": {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None},
        "sbom_cyclonedx": {
            "components": [
                {"name": "express", "version": "4.18.2"},
                {"name": "lodash", "version": "4.17.21"},
            ]
        },
        "risk_rankings": [
            {
                "dep_name": "express",
                "preliminary_score": 7.5,
                "risk_signals": ["CVE-2023-1234 HIGH"],
                "rationale": "has high CVE",
            },
            {
                "dep_name": "lodash",
                "preliminary_score": 3.0,
                "risk_signals": [],
                "rationale": "low risk",
            },
        ],
        "subgraph_results": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_risk_score_calls_agent_and_returns_fallback_on_empty_scores():
    """When mock agent doesn't call save_risk_score, fallback stub data is returned."""
    with patch("src.main_graph.nodes.risk_score.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_score(_base_state())

    mock_factory.assert_called_once()
    assert "risk_scores" in result
    assert len(result["risk_scores"]) == 2  # one per SBOM component
    dep_names = {s["dep_name"] for s in result["risk_scores"]}
    assert dep_names == {"express", "lodash"}
    # fallback scores
    assert all(s["score"] == 5.0 for s in result["risk_scores"])


@pytest.mark.asyncio
async def test_risk_score_falls_back_on_agent_exception():
    with patch("src.main_graph.nodes.risk_score.create_agent") as mock_factory:
        mock_factory.side_effect = RuntimeError("LLM unavailable")

        result = await risk_score(_base_state())

    assert len(result["risk_scores"]) == 2


@pytest.mark.asyncio
async def test_risk_score_adds_stubs_for_unscored_deps():
    """Partial scoring: agent scored one dep but not the other."""
    state = _base_state()

    def fake_factory(**kwargs):
        mock_agent = MagicMock()

        async def fake_ainvoke(inputs, config=None):
            # Simulate agent calling save_risk_score for only "express"
            # We can't directly call the closure, so this test relies on empty scores fallback.
            return {"messages": []}

        mock_agent.ainvoke = fake_ainvoke
        return mock_agent

    with patch("src.main_graph.nodes.risk_score.create_agent", side_effect=fake_factory):
        result = await risk_score(state)

    # Both deps must appear in output even if only partially scored
    dep_names = {s["dep_name"] for s in result["risk_scores"]}
    assert dep_names == {"express", "lodash"}


@pytest.mark.asyncio
async def test_risk_score_empty_sbom_returns_empty_scores():
    state = _base_state()
    state["sbom_cyclonedx"] = {}

    with patch("src.main_graph.nodes.risk_score.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_score(state)

    assert result["risk_scores"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_risk_score.py -v 2>&1 | head -20
```

Expected: ImportError or AssertionError — implementation is still a stub.

- [ ] **Step 3: Write the implementation**

Replace `src/main_graph/nodes/risk_score.py` entirely:

```python
"""risk_score — agentic node that computes final 0–10 risk scores per dep."""

from __future__ import annotations

import json
import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_DAOS
from src.utils.llm import Model, get_llm

_log = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_PROMPT = """\
You are a risk scoring agent. Compute a final 0–10 risk score for every \
dependency in scope, incorporating Stage 1 signals and Stage 2 impact findings.

Steps:
1. Call list_deps_to_score to get all deps that need a score.
2. For each dep:
   a. Call get_preliminary_ranking to see the initial risk assessment.
   b. Call get_vulnerabilities, get_registry_data, get_repo_data, get_runtime_data.
   c. Call get_impact_data if available (may return null for non-high-risk deps).
   d. Compute a final score (0–10) using this weighting guidance:
      - Vulnerabilities: up to 4.0 points (severity, count)
      - Maintenance: up to 2.5 points (deprecated, last_publish, maintainers)
      - Runtime: up to 2.0 points (test failures, lint errors)
      - License: up to 1.5 points (compliance violations)
      - Impact bonus: up to 1.0 extra if transitive_dependents is large
   e. Assign severity: critical (>=8), high (>=6), medium (>=4), low (<4).
   f. Call save_risk_score for this dep.
3. Call save_risk_score for EVERY dep before finishing.

save_risk_score parameters:
  dep_name (str), score (float 0–10), severity (str),
  breakdown (dict mapping dimension to float), rationale (str),
  impact_weight (float|null)
"""


async def risk_score(state: MainState) -> dict:
    plan_obj = state.get("plan") or {}
    dep_filter: list[str] | None = (
        plan_obj.get("dep_filter") if isinstance(plan_obj, dict) else None
    )
    sbom = state.get("sbom_cyclonedx") or {}
    all_deps = [c["name"] for c in sbom.get("components", [])]
    dep_scope = dep_filter if dep_filter else all_deps

    risk_rankings: list[dict] = state.get("risk_rankings") or []
    subgraph_results: list[dict] = state.get("subgraph_results") or []

    def _find_result_id(subgraph: str, dep_name: str | None = None) -> str | None:
        for r in subgraph_results:
            if r.get("subgraph") == subgraph and r.get("dep_name") == dep_name:
                return r.get("result_id")
        return None

    _scores: list[dict] = []

    @tool
    def list_deps_to_score() -> str:
        """Return all dependency names that need a final risk score."""
        return json.dumps(dep_scope)

    @tool
    def get_preliminary_ranking(dep_name: str) -> str:
        """Return the preliminary risk ranking for dep_name from risk_ranker."""
        for r in risk_rankings:
            if r.get("dep_name") == dep_name:
                return json.dumps(r)
        return json.dumps({})

    @tool
    async def get_vulnerabilities(dep_name: str) -> str:
        """Return vulnerability findings for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("vulnerabilities")
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["vulnerabilities"].get(result_id)
        if not doc:
            return json.dumps({})
        records = [r for r in doc.get("records", []) if r.get("name") == dep_name]
        return json.dumps({"records": records})

    @tool
    async def get_registry_data(dep_name: str) -> str:
        """Return npm registry health data for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("registry", dep_name)
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["registry"].get(result_id)
        return json.dumps(doc or {})

    @tool
    async def get_repo_data(dep_name: str) -> str:
        """Return GitHub repository signals for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("repo", dep_name)
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["repo"].get(result_id)
        return json.dumps(doc or {})

    @tool
    async def get_runtime_data(dep_name: str) -> str:
        """Return runtime test/lint results for dep_name from Stage 1 analysis."""
        result_id = _find_result_id("runtime", dep_name)
        if not result_id:
            return json.dumps({})
        doc = await SUBGRAPH_DAOS["runtime"].get(result_id)
        return json.dumps(doc or {})

    @tool
    async def get_impact_data(dep_name: str) -> str:
        """Return Stage 2 impact analysis for dep_name. Returns null if not analyzed."""
        result_id = _find_result_id("impact", dep_name)
        if not result_id:
            return json.dumps(None)
        doc = await SUBGRAPH_DAOS["impact"].get(result_id)
        return json.dumps(doc)

    @tool
    def save_risk_score(
        dep_name: str,
        score: float,
        severity: str,
        breakdown: dict,
        rationale: str,
        impact_weight: float | None = None,
    ) -> str:
        """Save the final risk score for dep_name.

        severity: one of critical, high, medium, low
        breakdown: dict mapping dimension name to float score contribution
        impact_weight: bonus points from impact analysis, or None if not analyzed
        """
        _scores.append(
            {
                "dep_name": dep_name,
                "score": score,
                "severity": severity,
                "breakdown": breakdown,
                "rationale": rationale,
                "impact_weight": impact_weight,
            }
        )
        return "ok"

    tools = [
        list_deps_to_score,
        get_preliminary_ranking,
        get_vulnerabilities,
        get_registry_data,
        get_repo_data,
        get_runtime_data,
        get_impact_data,
        save_risk_score,
    ]

    try:
        agent = create_agent(model=_llm, tools=tools)
        await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=f"Score the risk of these deps: {dep_scope}"
                    ),
                ]
            },
            config={"recursion_limit": 50},
        )
    except Exception:
        _log.exception("risk_score: agent failed")
        _scores.clear()

    # Fill in stubs for any dep the agent didn't score
    scored_deps = {s["dep_name"] for s in _scores}
    for dep in dep_scope:
        if dep not in scored_deps:
            _scores.append(
                {
                    "dep_name": dep,
                    "score": 5.0,
                    "severity": "medium",
                    "breakdown": {},
                    "rationale": "analysis unavailable",
                    "impact_weight": None,
                }
            )

    _log.info("risk_score: scored %d deps", len(_scores))
    return {"risk_scores": _scores}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_risk_score.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/risk_score.py tests/unit/nodes/test_risk_score.py
git commit -m "feat(risk_score): replace stub with agentic implementation"
```

---

## Task 3: recommendation_tools.py — npm/GitHub HTTP functions

**Files:**
- Create: `src/main_graph/nodes/recommendation_tools.py`
- Create: `tests/unit/nodes/test_recommendation_tools.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/nodes/test_recommendation_tools.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _search_npm ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_npm_returns_package_list():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "objects": [
            {
                "package": {
                    "name": "fastify",
                    "description": "Fast HTTP framework",
                    "date": "2024-01-01T00:00:00Z",
                }
            }
        ]
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _search_npm

        result = await _search_npm("http framework", max_results=5)

    assert len(result) == 1
    assert result[0]["name"] == "fastify"
    assert result[0]["description"] == "Fast HTTP framework"
    assert result[0]["last_publish"] == "2024-01-01T00:00:00Z"
    mock_client.get.assert_awaited_once()
    call_kwargs = mock_client.get.call_args
    assert "text" in call_kwargs.kwargs.get("params", {})


@pytest.mark.asyncio
async def test_search_npm_empty_results():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"objects": []}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _search_npm

        result = await _search_npm("nonexistent-xyz-package")

    assert result == []


# ── _get_npm_metadata ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_npm_metadata_extracts_fields():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "name": "express",
        "description": "Fast web framework",
        "dist-tags": {"latest": "4.18.2"},
        "license": "MIT",
        "homepage": "https://expressjs.com",
        "repository": {"url": "https://github.com/expressjs/express"},
        "maintainers": [{"name": "alice"}, {"name": "bob"}],
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _get_npm_metadata

        result = await _get_npm_metadata("express")

    assert result["name"] == "express"
    assert result["latest_version"] == "4.18.2"
    assert result["license"] == "MIT"
    assert result["maintainers"] == ["alice", "bob"]
    assert result["deprecated"] is None


# ── _get_github_summary ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_github_summary_returns_repo_info():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "stargazers_count": 12000,
        "open_issues_count": 45,
        "pushed_at": "2024-03-01T10:00:00Z",
        "archived": False,
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _get_github_summary

        result = await _get_github_summary("expressjs", "express")

    assert result["stars"] == 12000
    assert result["open_issues"] == 45
    assert result["archived"] is False


@pytest.mark.asyncio
async def test_get_github_summary_returns_empty_dict_on_http_error():
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
    )

    with patch(
        "src.main_graph.nodes.recommendation_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from src.main_graph.nodes.recommendation_tools import _get_github_summary

        result = await _get_github_summary("nonexistent", "repo")

    assert result == {}


# ── _compare_packages ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compare_packages_returns_metadata_for_all():
    express_meta = {"name": "express", "latest_version": "4.18.2", "license": "MIT"}
    fastify_meta = {"name": "fastify", "latest_version": "4.0.0", "license": "MIT"}

    call_count = 0

    async def fake_get_npm_metadata(name: str) -> dict:
        nonlocal call_count
        call_count += 1
        return express_meta if name == "express" else fastify_meta

    with patch(
        "src.main_graph.nodes.recommendation_tools._get_npm_metadata",
        side_effect=fake_get_npm_metadata,
    ):
        from src.main_graph.nodes.recommendation_tools import _compare_packages

        result = await _compare_packages("express", ["fastify"])

    assert "express" in result
    assert "fastify" in result
    assert call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_recommendation_tools.py -v 2>&1 | head -20
```

Expected: ImportError — `recommendation_tools` module not yet created.

- [ ] **Step 3: Write the implementation**

Create `src/main_graph/nodes/recommendation_tools.py`:

```python
"""HTTP lookup functions for the recommendation node — npm registry and GitHub."""

from __future__ import annotations

import os

import httpx

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_SEARCH = "https://registry.npmjs.org/-/v1/search"
_GITHUB_API = "https://api.github.com"


async def _search_npm(query: str, max_results: int = 10) -> list[dict]:
    """Search npm registry for packages matching query."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _NPM_SEARCH,
            params={"text": query, "size": max_results},
            timeout=10.0,
        )
        resp.raise_for_status()
        return [
            {
                "name": obj["package"]["name"],
                "description": obj["package"].get("description", ""),
                "weekly_downloads": None,
                "last_publish": obj["package"].get("date"),
            }
            for obj in resp.json().get("objects", [])
        ]


async def _get_npm_metadata(package_name: str) -> dict:
    """Fetch full npm registry metadata for package_name."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_NPM_REGISTRY}/{package_name}",
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        dist_tags = data.get("dist-tags", {})
        return {
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "latest_version": dist_tags.get("latest"),
            "license": data.get("license"),
            "deprecated": data.get("deprecated"),
            "homepage": data.get("homepage"),
            "repository": data.get("repository", {}).get("url"),
            "maintainers": [m.get("name") for m in data.get("maintainers", [])],
        }


async def _get_github_summary(owner: str, repo: str) -> dict:
    """Fetch key health signals from GitHub for owner/repo."""
    headers: dict[str, str] = {}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{_GITHUB_API}/repos/{owner}/{repo}",
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "stars": data.get("stargazers_count"),
                "open_issues": data.get("open_issues_count"),
                "last_commit": data.get("pushed_at"),
                "archived": data.get("archived", False),
            }
        except httpx.HTTPStatusError:
            return {}


async def _compare_packages(original: str, alternatives: list[str]) -> dict:
    """Fetch npm metadata for original and all alternatives for side-by-side comparison."""
    results: dict[str, dict] = {}
    for name in [original] + alternatives:
        try:
            results[name] = await _get_npm_metadata(name)
        except Exception:
            results[name] = {"name": name}
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_recommendation_tools.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/main_graph/nodes/recommendation_tools.py tests/unit/nodes/test_recommendation_tools.py
git commit -m "feat(recommendation): add npm/github HTTP lookup tools"
```

---

## Task 4: recommendation agentic node

**Files:**
- Modify: `src/main_graph/nodes/recommendation.py`
- Create: `tests/unit/nodes/test_recommendation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/nodes/test_recommendation.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.recommendation import recommendation


def _base_state():
    return {
        "high_risk_deps": ["express", "cookie"],
        "risk_scores": [
            {
                "dep_name": "express",
                "score": 7.5,
                "severity": "high",
                "breakdown": {"vulnerabilities": 4.0},
                "rationale": "has high CVE",
                "impact_weight": 0.5,
            },
            {
                "dep_name": "cookie",
                "score": 6.0,
                "severity": "high",
                "breakdown": {"maintenance": 2.5},
                "rationale": "deprecated",
                "impact_weight": None,
            },
        ],
        "subgraph_results": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_recommendation_calls_agent_and_returns_fallback_on_empty():
    """When mock agent doesn't call save_recommendation, fallback stubs returned."""
    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await recommendation(_base_state())

    mock_factory.assert_called_once()
    assert "recommendations" in result
    assert len(result["recommendations"]) == 2
    dep_names = {r["dep_name"] for r in result["recommendations"]}
    assert dep_names == {"express", "cookie"}
    # fallback has empty alternatives
    assert all(r["alternatives"] == [] for r in result["recommendations"])


@pytest.mark.asyncio
async def test_recommendation_falls_back_on_agent_exception():
    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_factory.side_effect = RuntimeError("LLM unavailable")

        result = await recommendation(_base_state())

    assert len(result["recommendations"]) == 2


@pytest.mark.asyncio
async def test_recommendation_empty_high_risk_deps_returns_empty():
    state = _base_state()
    state["high_risk_deps"] = []

    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await recommendation(state)

    assert result["recommendations"] == []
    # agent should not be called when there's nothing to do
    mock_factory.assert_not_called()


@pytest.mark.asyncio
async def test_recommendation_ainvoke_exception_returns_fallback():
    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("recursion limit"))
        mock_factory.return_value = mock_agent

        result = await recommendation(_base_state())

    assert len(result["recommendations"]) == 2
    assert all(r["alternatives"] == [] for r in result["recommendations"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_recommendation.py -v 2>&1 | head -20
```

Expected: tests fail because recommendation is still a stub.

- [ ] **Step 3: Write the implementation**

Replace `src/main_graph/nodes/recommendation.py` entirely:

```python
"""recommendation — agentic node that finds alternatives for high-risk deps."""

from __future__ import annotations

import json
import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.main_graph.nodes.recommendation_tools import (
    _compare_packages,
    _get_github_summary,
    _get_npm_metadata,
    _search_npm,
)
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_DAOS
from src.utils.llm import Model, get_llm

_log = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_PROMPT = """\
You are a dependency recommendation agent. For each high-risk dependency, \
find 1–3 actively maintained alternatives and explain the migration trade-off.

Deps to analyze: {high_risk_deps}

Steps for each dep:
1. Call get_risk_score to understand the risk profile.
2. Call get_impact_data to understand current usage and blast radius.
3. Call search_npm to find alternatives in the same problem space.
4. For each candidate, call get_npm_metadata and optionally get_github_summary.
5. Call compare_packages to get a side-by-side view.
6. Select the top 1–3 alternatives based on maintenance health, downloads, \
   license compatibility, and API similarity.
7. Call save_recommendation for this dep with a risk_summary, the selected \
   alternatives, and migration_notes.

For deps with no real alternatives (e.g. typescript, react), set alternatives=[] \
and explain why in migration_notes.

save_recommendation parameters:
  dep_name (str), risk_summary (str),
  alternatives (list of objects with keys: name, reason, weekly_downloads,
    last_publish, license, api_similarity, migration_effort),
  migration_notes (str)

api_similarity values: high | medium | low
migration_effort values: low | medium | high
"""


async def recommendation(state: MainState) -> dict:
    high_risk_deps: list[str] = state.get("high_risk_deps") or []

    if not high_risk_deps:
        return {"recommendations": []}

    risk_scores: list[dict] = state.get("risk_scores") or []
    subgraph_results: list[dict] = state.get("subgraph_results") or []

    def _find_result_id(subgraph: str, dep_name: str | None = None) -> str | None:
        for r in subgraph_results:
            if r.get("subgraph") == subgraph and r.get("dep_name") == dep_name:
                return r.get("result_id")
        return None

    _recs: list[dict] = []

    @tool
    def get_risk_score(dep_name: str) -> str:
        """Return the final risk score and breakdown for dep_name."""
        for s in risk_scores:
            if s.get("dep_name") == dep_name:
                return json.dumps(s)
        return json.dumps({})

    @tool
    async def get_impact_data(dep_name: str) -> str:
        """Return Stage 2 impact analysis for dep_name. Returns null if not analyzed."""
        result_id = _find_result_id("impact", dep_name)
        if not result_id:
            return json.dumps(None)
        doc = await SUBGRAPH_DAOS["impact"].get(result_id)
        return json.dumps(doc)

    @tool
    async def search_npm(query: str, max_results: int = 10) -> str:
        """Search npm registry for packages matching query.

        Returns list of {name, description, weekly_downloads, last_publish}.
        """
        results = await _search_npm(query, max_results)
        return json.dumps(results)

    @tool
    async def get_npm_metadata(package_name: str) -> str:
        """Fetch full npm registry metadata for package_name.

        Returns {name, description, latest_version, license, deprecated, homepage,
        repository, maintainers}.
        """
        data = await _get_npm_metadata(package_name)
        return json.dumps(data)

    @tool
    async def get_github_summary(owner: str, repo: str) -> str:
        """Fetch key health signals from GitHub for owner/repo.

        Returns {stars, open_issues, last_commit, archived}.
        Returns empty dict if not found or rate-limited.
        """
        data = await _get_github_summary(owner, repo)
        return json.dumps(data)

    @tool
    async def compare_packages(original: str, alternatives: list[str]) -> str:
        """Compare original and alternatives side-by-side using npm metadata.

        Returns dict mapping package name to its npm metadata.
        """
        data = await _compare_packages(original, alternatives)
        return json.dumps(data)

    @tool
    def save_recommendation(
        dep_name: str,
        risk_summary: str,
        alternatives: list[dict],
        migration_notes: str,
    ) -> str:
        """Save the recommendation for dep_name.

        alternatives: list of {name, reason, weekly_downloads, last_publish,
          license, api_similarity, migration_effort}
        """
        _recs.append(
            {
                "dep_name": dep_name,
                "risk_summary": risk_summary,
                "alternatives": alternatives,
                "migration_notes": migration_notes,
            }
        )
        return "ok"

    tools = [
        get_risk_score,
        get_impact_data,
        search_npm,
        get_npm_metadata,
        get_github_summary,
        compare_packages,
        save_recommendation,
    ]

    try:
        agent = create_agent(model=_llm, tools=tools)
        await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(
                        content=_SYSTEM_PROMPT.format(high_risk_deps=high_risk_deps)
                    ),
                    HumanMessage(
                        content=f"Find alternatives for: {high_risk_deps}"
                    ),
                ]
            },
            config={"recursion_limit": 50},
        )
    except Exception:
        _log.exception("recommendation: agent failed")
        _recs.clear()

    # Fill in stubs for any dep the agent didn't process
    processed_deps = {r["dep_name"] for r in _recs}
    for dep in high_risk_deps:
        if dep not in processed_deps:
            _recs.append(
                {
                    "dep_name": dep,
                    "risk_summary": "analysis unavailable",
                    "alternatives": [],
                    "migration_notes": "",
                }
            )

    _log.info("recommendation: processed %d high-risk deps", len(_recs))
    return {"recommendations": _recs}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/backend && uv run pytest tests/unit/nodes/test_recommendation.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full test suite to verify no regressions**

```bash
cd apps/backend && uv run pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: all tests PASS (83 + new tests).

- [ ] **Step 6: Commit**

```bash
git add src/main_graph/nodes/recommendation.py tests/unit/nodes/test_recommendation.py
git commit -m "feat(recommendation): replace stub with agentic implementation"
```
