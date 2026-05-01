"""Planner — internal function called by orchestrator to select analysis subgraphs."""

import json
import logging

from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import (
    SUBGRAPH_DESCRIPTIONS,
    SUBGRAPH_REGISTRY,
)
from src.utils.llm import Model, get_llm

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

_FALLBACK_PLAN: list[str] = ["registry", "risk_score", "recommendation"]


def _build_system_prompt() -> str:
    ingestion_lines = "\n".join(
        f"- {name}: {desc}"
        for entry in SUBGRAPH_DESCRIPTIONS
        for name, desc in [entry.split(":", 1)]
    )
    pipeline_lines = "\n".join(
        f"- {name}: {desc}" for name, desc in _PIPELINE_SUBGRAPHS
    )
    subgraph_lines = f"{ingestion_lines}\n{pipeline_lines}"
    example = json.dumps(["registry", "risk_score", "recommendation"])
    return (
        "You are a dependency analysis planner. Given a project's dependency"
        " discovery\nsummary, its direct and transitive dependencies, and a user"
        " concern, decide\nwhich analysis subgraphs to run. Available subgraphs:\n\n"
        f"{subgraph_lines}\n\n"
        f"Return ONLY a valid JSON array of subgraph names, e.g.: {example}\n"
        "Choose only the subgraphs relevant to the user's concern. Always include\n"
        '"risk_score" and "recommendation" when there are dependencies to analyze.\n'
        "If additional instructions are provided, honor them —\n"
        "they reflect updated user preferences."
    )


_SYSTEM_PROMPT = _build_system_prompt()


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
    deps = state.get("direct_dependencies", [])
    transitive_deps = state.get("transitive_dependencies", [])

    dep_list = ", ".join(d["name"] for d in deps[:20])
    if len(deps) > 20:
        dep_list += f", and {len(deps) - 20} more"

    transitive_dep_list = ", ".join(d["name"] for d in transitive_deps[:20])
    if len(transitive_deps) > 20:
        transitive_dep_list += f", and {len(transitive_deps) - 20} more"

    user_message = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Direct dependencies ({len(deps)}): {dep_list}\n"
        f"Transitive dependencies ({len(transitive_deps)}): {transitive_dep_list}"
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
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        plan = json.loads(raw.strip())
        plan = [s for s in plan if s in VALID_SUBGRAPHS]
        if not plan:
            plan = _FALLBACK_PLAN
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("run_planner: failed to parse LLM response, using fallback plan")
        plan = _FALLBACK_PLAN

    logger.info("run_planner: selected subgraphs: %s", plan)
    return plan
