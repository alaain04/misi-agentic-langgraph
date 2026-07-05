"""Investigation planner business logic."""
from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.main_graph.constants import INVESTIGATION_PLANNER
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

_DEP_SELECTOR_SYSTEM = """\
You are a dependency relevance ranker.

Given a user's security concern and a list of transitive dependency names,
return the names most relevant to investigate, ordered by relevance to the concern.

Output ONLY a valid JSON array of dependency names, most relevant first:
["dep1", "dep2", ...]
"""

_SELECTOR_THRESHOLD = 30   # max transitive deps before LLM ranking kicks in
_MAX_SELECTED_TRANSITIVES = 20

_INTENT_SYSTEM = """\
Classify the user's response to a proposed investigation plan as one of:
  - approve: user is satisfied and wants to proceed
  - change: user wants modifications
  - cancel: user wants to abort

Return ONLY one word: approve, change, or cancel.
"""


def _get_direct_dep_names(sbom: dict) -> set[str]:
    """Returns names of direct dependencies from the CycloneDX dependencies section."""
    root_ref = sbom.get("metadata", {}).get("component", {}).get("bom-ref")
    if not root_ref:
        return set()
    ref_to_name = {
        c.get("bom-ref"): c.get("name")
        for c in sbom.get("components", [])
        if c.get("bom-ref")
    }
    for entry in sbom.get("dependencies", []):
        if entry.get("ref") == root_ref:
            return {ref_to_name[r] for r in entry.get("dependsOn", []) if r in ref_to_name}
    return set()


async def _rank_transitive_deps(concern: str, transitive: list[dict]) -> list[str]:
    """Ask the LLM to rank transitive deps by relevance to the concern."""
    names_text = "\n".join(c["name"] for c in transitive)
    response = await _llm.ainvoke([
        {"role": "system", "content": _DEP_SELECTOR_SYSTEM},
        {"role": "user", "content": f"Concern: {concern}\n\nTransitive dependencies:\n{names_text}"},
    ])
    result = parse_llm_json(response.content or "")
    if isinstance(result, list):
        return result
    return [c["name"] for c in transitive[:_MAX_SELECTED_TRANSITIVES]]


async def _select_deps(concern: str, components: list[dict], sbom: dict) -> list[dict]:
    """Returns direct deps + LLM-ranked transitive deps for large SBOMs."""
    direct_names = _get_direct_dep_names(sbom)
    direct = [c for c in components if c.get("name") in direct_names]
    transitive = [c for c in components if c.get("name") not in direct_names]

    if len(transitive) <= _SELECTOR_THRESHOLD:
        return components

    ranked = await _rank_transitive_deps(concern, transitive)
    top_names = set(ranked[:_MAX_SELECTED_TRANSITIVES])
    selected_transitive = [c for c in transitive if c.get("name") in top_names]
    return direct + selected_transitive


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
    selected = await _select_deps(concern, components, sbom)
    comp_list = ", ".join(c["name"] for c in selected)

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


async def _get_artifact(dao: JobRepositoryPort, job_id: str, node: str) -> dict:
    """Return the artifact dict for `node` from the stored job, or {}."""
    job = await dao.get(job_id)
    if not job:
        return {}
    return next((a for a in job.artifacts if a.get("node") == node), {})


def _reconstruct_plan(artifact: dict, concern: str) -> InvestigationPlan | None:
    """Reconstruct InvestigationPlan from stored artifact data, or return None."""
    plan_data = artifact.get("data", {}).get("plan")
    if not plan_data:
        return None
    try:
        hypotheses = [Hypothesis(**h) for h in plan_data.get("hypotheses", [])]
    except Exception:
        return None
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
        rationale=plan_data.get("rationale", ""),
        dep_filter=plan_data.get("dep_filter"),
    )


async def investigation_planner_service(
    state: MainState,
    dao: JobRepositoryPort,
    vector_store=None,
) -> dict | Command:
    """HITL loop: present plan, classify intent, loop on change, exit on approve/cancel."""
    job_id = state["job_id"]
    concern = state.get("concern", "")

    # Re-run detection: LangGraph re-executes this entire node from scratch when
    # resuming from interrupt(). If artifact already has stored plan data, use it
    # instead of re-calling the LLM — avoids duplicate messages and wrong-plan bugs.
    artifact = await _get_artifact(dao, job_id, INVESTIGATION_PLANNER)
    stored_plan = _reconstruct_plan(artifact, concern)
    if stored_plan is not None:
        logger.info("investigation_planner: restoring plan from artifact (hypotheses=%d)", len(stored_plan.hypotheses))
        plan = stored_plan
    else:
        plan = await _run_planner(state)
        logger.info(
            "investigation_planner: plan generated hypotheses=%d skill_assignments=%d",
            len(plan.hypotheses), len(plan.skill_plan),
        )

    while True:
        messages = artifact.get("messages", [])
        is_rerun = bool(messages) and messages[-1].get("role") == "assistant"

        if is_rerun:
            assistant_msg = messages[-1]["content"]
        else:
            assistant_msg = _present_plan(plan)
            created_at = datetime.now(UTC).isoformat()
            await dao.push_artifact_message(job_id, INVESTIGATION_PLANNER, {
                "role": "assistant",
                "content": assistant_msg,
                "created_at": created_at,
            })
            await dao.update_artifact_data(job_id, INVESTIGATION_PLANNER, {
                "data": {
                    "plan": {
                        "hypotheses": [dataclasses.asdict(h) for h in plan.hypotheses],
                        "rationale": plan.rationale,
                        "dep_filter": plan.dep_filter,
                    }
                }
            })

        user_input: str = interrupt({
            "investigation_plan": dataclasses.asdict(plan),
            "assistant_message": assistant_msg,
        })

        if vector_store:
            try:
                await vector_store.add_texts([f"Assistant: {assistant_msg}", f"User: {user_input}"])
            except Exception:
                logger.warning("investigation_planner: vector store add failed")

        intent = await _classify_intent(plan, user_input)
        logger.info("investigation_planner: user intent=%s", intent)
        await dao.push_artifact_message(job_id, INVESTIGATION_PLANNER, {
            "role": "human",
            "content": user_input,
            "created_at": datetime.now(UTC).isoformat(),
            "action": intent,
        })

        new_messages = [AIMessage(content=assistant_msg), HumanMessage(content=user_input)]

        if intent == "approve":
            new_messages.append(AIMessage(content="Plan approved! Investigation is starting now."))
            return {"investigation_plan": plan, "messages": new_messages}

        if intent == "cancel":
            return Command(goto=END, update={"cancelled": True, "messages": new_messages})

        plan = await _run_planner(state, extra_instructions=user_input)
        # Reload artifact so next iteration sees the human message as last → not a re-run
        artifact = await _get_artifact(dao, job_id, INVESTIGATION_PLANNER)
