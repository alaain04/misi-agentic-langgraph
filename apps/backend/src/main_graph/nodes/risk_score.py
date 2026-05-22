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
  impact_weight (float or null)
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
        """Save the final risk score for dep_name. severity: critical|high|medium|low. breakdown: dict of dimension to float."""
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
        get_license_compliance,
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

    # Fill stubs for any dep the agent didn't score
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
