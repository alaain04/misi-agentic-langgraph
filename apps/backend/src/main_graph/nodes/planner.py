"""Planner — selects analysis subgraphs and optional dep_filter based on concern."""

import json
import logging

from src.main_graph.plan import Plan
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DESCRIPTIONS,
    SUBGRAPH_REGISTRY,
)
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_PIPELINE_SUBGRAPHS: list[tuple[str, str]] = [
    ("risk_score", "Computes a composite 0–10 risk score per dependency from all analysis signals"),
    ("recommendation", "Finds 1–3 maintained alternatives for each high-risk dependency"),
]

VALID_SUBGRAPHS: set[str] = set(SUBGRAPH_REGISTRY.keys()) | {
    name for name, _ in _PIPELINE_SUBGRAPHS
}

_FALLBACK_PLAN: Plan = {"subgraphs": ["vulnerabilities", "risk_score", "recommendation"], "dep_filter": None}

_ingestion_lines = "\n".join(
    f"- {name}: {desc}"
    for entry in SUBGRAPH_DESCRIPTIONS
    for name, desc in [entry.split(":", 1)]
)
_pipeline_lines = "\n".join(f"- {name}: {desc}" for name, desc in _PIPELINE_SUBGRAPHS)
_example = json.dumps({"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None})
_example_filter = json.dumps({"subgraphs": ["registry", "repo", "runtime"], "dep_filter": ["react", "lodash"]})

_SYSTEM_PROMPT = f"""\
You are a dependency analysis planner. Given a project's dependency discovery
summary, its components, and a user concern, decide which analysis subgraphs
to run. Available subgraphs:

{_ingestion_lines}
{_pipeline_lines}

Return ONLY a valid JSON object with keys:
  "subgraphs": array of subgraph names relevant to the user's concern
  "dep_filter": array of specific package names to focus on, or null for all dependencies

Examples:
  Security concern: {_example}
  Specific dep concern: {_example_filter}

risk_score and recommendation always run and do not need to be included in subgraphs.
If additional instructions are provided, honor them — they reflect updated user preferences.
"""


async def run_planner(state: MainState, extra_instructions: str = "") -> Plan:
    """Select subgraphs and optional dep_filter based on discovery context and concern.

    Args:
        state: current MainState (used for discovery context and concern)
        extra_instructions: optional user feedback to steer or refine the plan

    Returns:
        Plan with subgraphs to execute and optional dep_filter
    """
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    sbom = state.get("sbom_cyclonedx", {})

    components = sbom.get("components", [])
    comp_list = ", ".join(c["name"] for c in components[:30])
    if len(components) > 30:
        comp_list += f", and {len(components) - 30} more"

    user_message = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Components ({len(components)}): {comp_list}"
    )
    if extra_instructions:
        user_message += f"\n\nAdditional instructions from the user: {extra_instructions}"

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    )

    try:
        parsed = parse_llm_json(response.content or "")
        if isinstance(parsed, list):
            subgraphs = [s for s in parsed if s in VALID_SUBGRAPHS]
            plan: Plan = {"subgraphs": subgraphs or _FALLBACK_PLAN["subgraphs"], "dep_filter": None}
        elif isinstance(parsed, dict):
            subgraphs = [s for s in (parsed.get("subgraphs") or []) if s in VALID_SUBGRAPHS]
            plan = {"subgraphs": subgraphs or _FALLBACK_PLAN["subgraphs"], "dep_filter": parsed.get("dep_filter")}
        else:
            plan = _FALLBACK_PLAN
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("run_planner: failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("run_planner: selected subgraphs=%s dep_filter=%s", plan["subgraphs"], plan.get("dep_filter"))
    return plan
