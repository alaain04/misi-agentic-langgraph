"""Planner node — selects analysis subgraphs via LLM."""

import json
import logging

from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DESCRIPTIONS,
    SUBGRAPH_REGISTRY,
)
from src.main_graph.subgraphs.orchestrator.state import OrchestratorState
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

VALID_SUBGRAPHS: set[str] = set(SUBGRAPH_REGISTRY.keys())
_FALLBACK_PLAN: list[str] = list(SUBGRAPH_REGISTRY.keys())[:2]


def _build_system_prompt() -> str:
    subgraph_lines = "\n".join(
        f"- {name}: {desc}"
        for entry in SUBGRAPH_DESCRIPTIONS
        for name, desc in [entry.split(":", 1)]
    )
    example = json.dumps(list(SUBGRAPH_REGISTRY.keys())[:2])
    return (
        "You are a dependency analysis planner. Given a project's dependency"
        " discovery summary, its SBOM component list, and a user concern, decide"
        " which analysis subgraphs to run. Available subgraphs:\n"
        f"{subgraph_lines}\n"
        f"Return ONLY a valid JSON array of subgraph names, e.g.: {example}\n"
        "Choose only the subgraphs relevant to the user's concern.\n"
        "If additional instructions are provided, honor them —\n"
        "they reflect updated user preferences."
    )


_SYSTEM_PROMPT = _build_system_prompt()


async def run_planner(
    state: OrchestratorState, extra_instructions: str = ""
) -> list[str]:
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    sbom = state.get("sbom_cyclonedx", {})

    components = sbom.get("components", [])
    dep_list = ", ".join(c["name"] for c in components[:20])
    if len(components) > 20:
        dep_list += f", and {len(components) - 20} more"

    user_message = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Total components ({len(components)}): {dep_list}"
    )
    if extra_instructions:
        user_message += (
            f"\n\nAdditional instructions from the user: {extra_instructions}"
        )

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    )

    try:
        plan = parse_llm_json(response.content or "")
        plan = [s for s in plan if s in VALID_SUBGRAPHS]
        if not plan:
            plan = _FALLBACK_PLAN
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("planner: failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("planner: selected subgraphs: %s", plan)
    return plan


async def planner(state: OrchestratorState) -> dict:
    plan = await run_planner(
        state, extra_instructions=state.get("extra_instructions", "")
    )
    return {"plan": plan, "extra_instructions": ""}
