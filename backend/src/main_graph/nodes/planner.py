"""Planner — internal function called by orchestrator to select analysis subgraphs."""

import json
import logging

from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DESCRIPTIONS,
    SUBGRAPH_REGISTRY,
)
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

# Non-ingestion subgraphs that are always part of the main pipeline.
_PIPELINE_SUBGRAPHS: list[tuple[str, str]] = [
    (
        "risk_score",
        "Computes a composite risk score from all available analysis signals",
    ),
    (
        "recommendation",
        "Generates actionable remediation recommendations based on identified risks",
    ),
]

VALID_SUBGRAPHS: set[str] = set(SUBGRAPH_REGISTRY.keys()) | {
    name for name, _ in _PIPELINE_SUBGRAPHS
}

_FALLBACK_PLAN: list[str] = ["vulnerabilities", "risk_score", "recommendation"]

_ingestion_lines = "\n".join(
    f"- {name}: {desc}"
    for entry in SUBGRAPH_DESCRIPTIONS
    for name, desc in [entry.split(":", 1)]
)
_pipeline_lines = "\n".join(f"- {name}: {desc}" for name, desc in _PIPELINE_SUBGRAPHS)
_example = json.dumps(_FALLBACK_PLAN)

_SYSTEM_PROMPT = f"""\
You are a dependency analysis planner. Given a project's dependency discovery
summary, its components, and a user concern, decide which analysis subgraphs
to run. Available subgraphs:

{_ingestion_lines}
{_pipeline_lines}

Return ONLY a valid JSON array of subgraph names, e.g.: {_example}
Choose only the subgraphs relevant to the user's concern.
If additional instructions are provided, honor them — they reflect updated
user preferences.
"""


async def run_planner(state: MainState, extra_instructions: str = "") -> list[str]:
    """\
    Select subgraphs to run based on discovery context and optional user instructions.

    Args:
        state: current MainState (used for discovery context and concern)
        extra_instructions: optional user feedback to steer or refine the plan

    Returns:
        list of subgraph names to execute
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
        logger.warning("run_planner: failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("run_planner: selected subgraphs: %s", plan)
    return plan
