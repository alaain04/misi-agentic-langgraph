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

Steps for each dep:
1. Call get_risk_score to understand the risk profile.
2. Call get_impact_data to understand current usage and blast radius.
3. Call search_npm to find alternatives in the same problem space.
4. For each candidate, call get_npm_metadata and optionally get_github_summary.
5. Call compare_packages to get a side-by-side view.
6. Select the top 1–3 alternatives based on maintenance health, weekly_downloads, \
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
        """Search npm registry for packages matching query. Returns list of {name, description, weekly_downloads, last_publish}."""
        results = await _search_npm(query, max_results)
        return json.dumps(results)

    @tool
    async def get_npm_metadata(package_name: str) -> str:
        """Fetch full npm registry metadata for package_name. Returns {name, description, latest_version, license, deprecated, homepage, repository, maintainers}."""
        data = await _get_npm_metadata(package_name)
        return json.dumps(data)

    @tool
    async def get_github_summary(owner: str, repo: str) -> str:
        """Fetch key health signals from GitHub for owner/repo. Returns {stars, open_issues, last_commit, archived}. Returns empty dict if unavailable."""
        data = await _get_github_summary(owner, repo)
        return json.dumps(data)

    @tool
    async def compare_packages(original: str, alternatives: list[str]) -> str:
        """Compare original and alternatives side-by-side using npm metadata. Returns dict mapping package name to its npm metadata."""
        data = await _compare_packages(original, alternatives)
        return json.dumps(data)

    @tool
    def save_recommendation(
        dep_name: str,
        risk_summary: str,
        alternatives: list[dict],
        migration_notes: str,
    ) -> str:
        """Save the recommendation for dep_name. alternatives: list of {name, reason, weekly_downloads, last_publish, license, api_similarity, migration_effort}."""
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
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=f"Find alternatives for: {high_risk_deps}"
                    ),
                ]
            },
            config={"recursion_limit": 50},
        )
    except Exception:
        _log.exception("recommendation: agent failed")

    # Fill stubs for any high-risk dep the agent didn't process
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
