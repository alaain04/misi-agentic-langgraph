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

    @tool
    def save_ranking(rankings: list[dict]) -> str:
        """Save the final risk ranking. rankings: list of {dep_name, preliminary_score, risk_signals, rationale}."""
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

    ranked_deps = {r["dep_name"] for r in _rankings}
    for dep in dep_scope:
        if dep not in ranked_deps:
            _rankings.append(
                {
                    "dep_name": dep,
                    "preliminary_score": 5.0,
                    "risk_signals": [],
                    "rationale": "analysis unavailable",
                }
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
