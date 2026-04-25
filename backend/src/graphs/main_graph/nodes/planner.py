"""Planner node — LLM decides which subgraphs to run based on discovery output."""

import json
import logging

from src.graphs.main_graph.state import MainState
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_VALID_SUBGRAPHS = {"registry", "repo", "runtime", "risk_score", "recommendation"}

_FALLBACK_PLAN = ["registry", "risk_score", "recommendation"]

_SYSTEM_PROMPT = """\
You are a dependency analysis planner. Given a project's dependency discovery
summary, its direct dependencies, and a user concern, decide which analysis
subgraphs to run. Available subgraphs:

- registry: checks npm registry for outdated versions and vulnerability advisories
- repo: analyzes the GitHub repository for stars, issues, last commit, maintenance 
status
- runtime: checks runtime compatibility and environment configuration
- risk_score: computes a composite risk score from all available signals
- recommendation: generates actionable remediation recommendations

Return ONLY a valid JSON array of subgraph names, e.g.: ["registry", "risk_score"]
Choose only the subgraphs relevant to the user's concern. Always include
"risk_score" and "recommendation" when there are dependencies to analyze.\
"""


async def planner(state: MainState) -> dict:
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    deps = state.get("direct_dependencies", [])

    dep_list = ", ".join(d["name"] for d in deps[:20])
    if len(deps) > 20:
        dep_list += f", and {len(deps) - 20} more"

    user_message = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Direct dependencies ({len(deps)}): {dep_list}"
    )

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    )

    try:
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        plan = json.loads(raw.strip())
        plan = [s for s in plan if s in _VALID_SUBGRAPHS]
        if not plan:
            plan = _FALLBACK_PLAN
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("planner failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("planner selected subgraphs: %s", plan)
    return {"plan": plan}
