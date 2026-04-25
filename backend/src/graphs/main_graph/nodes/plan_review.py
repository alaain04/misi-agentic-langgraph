"""Plan review node — pauses execution for human approval of the analysis plan."""

import json
import logging

from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.graphs.main_graph.nodes.planner import _VALID_SUBGRAPHS
from src.graphs.main_graph.state import MainState
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_REFINE_SYSTEM_PROMPT = """\
You are a dependency analysis planner. Given the current analysis plan, a project summary,
and user feedback, update the plan to address the feedback.

Available subgraphs:
- registry: checks npm registry for outdated versions and vulnerability advisories
- repo: analyzes the GitHub repository for stars, issues, last commit, maintenance status
- runtime: checks runtime compatibility and environment configuration
- risk_score: computes a composite risk score from all available signals
- recommendation: generates actionable remediation recommendations

Return ONLY a valid JSON array of subgraph names, e.g.: ["registry", "risk_score"]
Honor the user's intent. Always include "risk_score" and "recommendation" unless the
user explicitly asks to remove them.\
"""


async def _refine_plan(current_plan: list[str], state: MainState, feedback: str) -> list[str]:
    dep_count = len(state.get("direct_dependencies", []))
    user_message = (
        f"Current plan: {json.dumps(current_plan)}\n"
        f"Discovery summary: {state.get('discovery_summary', '')}\n"
        f"Direct dependencies: {dep_count}\n"
        f"User feedback: {feedback}"
    )
    try:
        response = await _llm.ainvoke([
            {"role": "system", "content": _REFINE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ])
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        refined = json.loads(raw.strip())
        refined = [s for s in refined if s in _VALID_SUBGRAPHS]
        return refined if refined else current_plan
    except Exception:
        logger.warning("plan_review: failed to refine plan with LLM, keeping current plan")
        return current_plan


async def plan_review(state: MainState):
    """Interrupt to get human approval/modification of the LLM-generated plan.

    Supports a refine loop: the user can send written feedback multiple times to
    update the plan before approving or cancelling.
    """
    current_plan = state["plan"]

    while True:
        decision = interrupt({
            "plan": current_plan,
            "discovery_summary": state.get("discovery_summary", ""),
            "direct_dependencies_count": len(state.get("direct_dependencies", [])),
        })

        action = decision.get("action")

        if action == "cancel":
            return Command(goto=END, update={"plan": []})

        if action in ("approve", "modify"):
            modified = decision.get("plan")
            if modified is not None:
                final_plan = [s for s in modified if s in _VALID_SUBGRAPHS] or current_plan
            else:
                final_plan = current_plan
            return {"plan": final_plan}

        if action == "refine":
            feedback = decision.get("feedback", "")
            logger.info("plan_review: refining plan with feedback: %r", feedback)
            current_plan = await _refine_plan(current_plan, state, feedback)
            logger.info("plan_review: refined plan: %s", current_plan)
            # Loop continues — next interrupt() will show the updated plan
