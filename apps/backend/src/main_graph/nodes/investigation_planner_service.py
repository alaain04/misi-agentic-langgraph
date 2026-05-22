"""Investigation planner business logic."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph.skills.registry import SKILL_DESCRIPTIONS, SKILL_REGISTRY
from src.main_graph.state import MainState
from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment
from src.utils.llm import Model, get_llm, parse_llm_json

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_PLANNER_SYSTEM = """\
You are a dependency risk investigation planner.

Given a project's SBOM and a user concern, you must:
1. Generate risk hypotheses for the most relevant dependencies.
   Each hypothesis is a falsifiable statement about a specific risk.
   Example: "lodash@4.17.20 may expose the project to prototype pollution attacks"
2. Assign investigation skills to each hypothesis.
   Choose skills whose trigger_conditions match the hypothesis risk_theme.
3. Explain your rationale.

Available skills:
{skill_descriptions}

Output ONLY a valid JSON object:
{{
  "hypotheses": [
    {{
      "id": "h1",
      "dep_name": "<package name>",
      "statement": "<falsifiable risk statement>",
      "risk_theme": "<vulnerability|supply_chain|maintainer|license|reachability|blast_radius>",
      "rationale": "<why this hypothesis>",
      "skills": ["<SkillId>"]
    }}
  ],
  "dep_filter": null,
  "rationale": "<overall plan rationale>"
}}
"""

_INTENT_SYSTEM = """\
Classify the user's response to a proposed investigation plan as one of:
  - approve: user is satisfied and wants to proceed
  - change: user wants modifications
  - cancel: user wants to abort

Return ONLY one word: approve, change, or cancel.
"""


def _build_skill_descriptions() -> str:
    return "\n".join(f"- {sid}: {desc}" for sid, desc in SKILL_DESCRIPTIONS.items())


def _parse_investigation_plan(parsed: dict, concern: str) -> InvestigationPlan:
    hypotheses = [
        Hypothesis(
            id=h["id"],
            dep_name=h["dep_name"],
            statement=h["statement"],
            risk_theme=h["risk_theme"],
            rationale=h["rationale"],
            skills=h["skills"],
        )
        for h in parsed.get("hypotheses", [])
    ]
    skill_plan = [
        SkillAssignment(dep_name=h.dep_name, hypothesis_id=h.id, skill_id=sid)
        for h in hypotheses
        for sid in h.skills
        if sid in SKILL_REGISTRY
    ]
    return InvestigationPlan(
        concern=concern,
        hypotheses=hypotheses,
        skill_plan=skill_plan,
        rationale=parsed.get("rationale", ""),
        dep_filter=parsed.get("dep_filter"),
    )


def _present_plan(plan: InvestigationPlan) -> str:
    lines = ["**Proposed Investigation Plan:**\n", f"*{plan.rationale}*\n"]
    for i, h in enumerate(plan.hypotheses, 1):
        skill_names = [SKILL_REGISTRY[sid].name for sid in h.skills if sid in SKILL_REGISTRY]
        lines.append(f"{i}. **{h.dep_name}**: {h.statement}")
        lines.append(f"   Skills: {', '.join(skill_names)}")
    if plan.dep_filter:
        lines.append(f"\n**Scope:** {', '.join(plan.dep_filter)}")
    lines.append("\nWould you like to proceed, request changes, or cancel?")
    return "\n".join(lines)


async def _run_planner(state: MainState, extra_instructions: str = "") -> InvestigationPlan:
    """Call the LLM to produce an InvestigationPlan from current state."""
    concern = state.get("concern", "")
    summary = state.get("discovery_summary", "")
    sbom = state.get("sbom_cyclonedx", {})
    components = sbom.get("components", [])
    comp_list = ", ".join(c["name"] for c in components[:30])
    if len(components) > 30:
        comp_list += f", and {len(components) - 30} more"

    user_msg = (
        f"Concern: {concern}\n"
        f"Discovery summary: {summary}\n"
        f"Components ({len(components)}): {comp_list}"
    )
    if extra_instructions:
        user_msg += f"\n\nAdditional instructions: {extra_instructions}"

    response = await _llm.ainvoke([
        {"role": "system", "content": _PLANNER_SYSTEM.format(skill_descriptions=_build_skill_descriptions())},
        {"role": "user", "content": user_msg},
    ])
    parsed = parse_llm_json(response.content or "")
    return _parse_investigation_plan(parsed, concern)


async def _classify_intent(plan: InvestigationPlan, user_input: str) -> str:
    """Classify user response as approve, change, or cancel."""
    plan_str = "\n".join(f"{i+1}. {h.statement}" for i, h in enumerate(plan.hypotheses))
    response = await _llm.ainvoke([
        {"role": "system", "content": _INTENT_SYSTEM},
        {"role": "user", "content": f"Plan:\n{plan_str}\n\nUser: {user_input}"},
    ])
    intent = response.content.strip().lower()
    return intent if intent in ("approve", "change", "cancel") else "change"


async def investigation_planner_service(
    state: MainState,
    dao: JobRepositoryPort,
    vector_store=None,
) -> dict | Command:
    """HITL loop: present plan, classify intent, loop on change, exit on approve/cancel."""
    job_id = state["job_id"]
    plan = await _run_planner(state)

    while True:
        assistant_msg = _present_plan(plan)
        created_at = datetime.now(UTC).isoformat()

        await dao.push_proposal(job_id, {
            "created_at": created_at,
            "plan": {"hypotheses": [h.__dict__ for h in plan.hypotheses], "rationale": plan.rationale},
            "assistant_message": assistant_msg,
        })

        user_input: str = interrupt({
            "investigation_plan": plan.__dict__,
            "assistant_message": assistant_msg,
        })

        if vector_store:
            try:
                await vector_store.add_texts([f"Assistant: {assistant_msg}", f"User: {user_input}"])
            except Exception:
                logger.warning("investigation_planner: vector store add failed")

        intent = await _classify_intent(plan, user_input)
        await dao.update_proposal(job_id, created_at=created_at, user_response=user_input, intent=intent)

        new_messages = [AIMessage(content=assistant_msg), HumanMessage(content=user_input)]

        if intent == "approve":
            new_messages.append(AIMessage(content="Plan approved! Investigation is starting now."))
            return {"investigation_plan": plan, "messages": new_messages}

        if intent == "cancel":
            return Command(goto=END, update={"cancelled": True, "messages": new_messages})

        plan = await _run_planner(state, extra_instructions=user_input)
