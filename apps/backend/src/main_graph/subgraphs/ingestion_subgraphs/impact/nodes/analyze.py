"""Impact analysis node — agentic implementation."""

from __future__ import annotations

import json
import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.main_graph.subgraphs.ingestion_subgraphs.impact.dao import impact_dao
from src.main_graph.subgraphs.ingestion_subgraphs.impact.models import ImpactEntry
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState
from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.filesystem import (
    find_usages,
    list_source_files,
    read_file_excerpt,
)
from src.main_graph.subgraphs.ingestion_subgraphs.impact.tools.sbom_tools import (
    compute_blast_radius,
    compute_direct_dependents,
)
from src.utils.llm import Model, get_llm

_log = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM_PROMPT = """\
You are an impact analysis agent for JavaScript/TypeScript projects.

Your task: analyze how the dependency '{dep_name}' is used inside the project
located at '{repo_path}'.

Steps:
1. Use list_source_files to enumerate all source files.
2. Use find_usages to find all import/require statements for '{dep_name}'.
3. Use read_file_excerpt to read up to 10 representative usage sites and
   identify which parts of the API are used (e.g. specific named exports,
   constructor calls, middleware use).
4. Use get_direct_dependents and get_blast_radius to understand the
   transitive impact if this dep changes.
5. Write a concise usage_summary (2-3 sentences) and blast_radius_summary
   (1-2 sentences).

Return a structured result with all ImpactEntry fields populated.
"""


async def analyze(state: ImpactState) -> dict:
    dep_name = state.get("dependency_name", "")
    repo_path = state.get("repo_path", "")

    if not dep_name or not repo_path:
        result_id = await impact_dao.save(ImpactEntry(dep_name=dep_name))
        return {"result_id": result_id}

    sbom = state.get("sbom_cyclonedx", {})

    @tool
    def get_direct_dependents(target: str) -> str:
        """Return package names that directly depend on target in the SBOM."""
        return json.dumps(compute_direct_dependents(target, sbom))

    @tool
    def get_blast_radius(target: str) -> str:
        """Compute blast radius for target. Returns JSON with keys: direct_dependents, transitive_dependents, max_depth."""
        return json.dumps(compute_blast_radius(target, sbom))

    tools = [
        list_source_files,
        find_usages,
        read_file_excerpt,
        get_direct_dependents,
        get_blast_radius,
    ]

    try:
        agent = create_agent(
            model=_llm,
            tools=tools,
            response_format=ImpactEntry,
        )
        result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(
                        content=_SYSTEM_PROMPT.format(
                            dep_name=dep_name,
                            repo_path=repo_path,
                        )
                    ),
                    HumanMessage(content=f"Analyze the impact of '{dep_name}' now."),
                ]
            },
            config={"recursion_limit": 30},
        )
        entry: ImpactEntry = result["structured_response"]
        entry.dep_name = dep_name
    except Exception:
        _log.exception("impact.analyze: agent failed for dep=%s", dep_name)
        entry = ImpactEntry(dep_name=dep_name)

    result_id = await impact_dao.save(entry)
    _log.info("impact.analyze: saved result_id=%s dep=%s", result_id, dep_name)
    return {"result_id": result_id}
