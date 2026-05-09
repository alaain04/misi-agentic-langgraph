"""Orchestrator node — presents plan, pauses for human approval, routes on intent."""

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.main_graph.subgraphs.orchestrator.state import OrchestratorState
from src.services.job_dao import JobDAO
from src.services.vector_store import get_or_create_store
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

_TOP_K = 4

_PRESENT_SYSTEM_PROMPT = """\
You are a dependency analysis assistant presenting an analysis plan to the user.
Present the plan clearly and concisely, then ask for their approval or feedback.
Format the selected subgraphs as a numbered list with a one-line description of each.
End with: "Would you like to proceed with this plan, request changes, or cancel?"\
"""

_INTENT_SYSTEM_PROMPT = """\
You are classifying a user's response to a proposed dependency analysis plan.
The user was shown the plan and asked whether to proceed, request changes, or cancel.

Classify their intent as exactly one of:
  - approve: user is satisfied and wants to proceed
  - change: user wants modifications, has concerns, or provides new instructions
  - cancel: user wants to abort the analysis entirely

Return ONLY one word: approve, change, or cancel.\
"""


async def _present_plan(plan: list[str], state: OrchestratorState, context: str) -> str:
    plan_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan))
    sbom = state.get("sbom_cyclonedx", {})
    user_content = (
        f"Project concern: {state.get('concern', 'not specified')}\n"
        f"Total components: {len(sbom.get('components', []))}\n"
    )
    if context:
        user_content += f"\nPrior conversation context:\n{context}\n"
    user_content += f"\nProposed analysis plan:\n{plan_str}"

    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _PRESENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )
    return response.content


async def _classify_intent(plan: list[str], user_input: str) -> str:
    plan_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan))
    response = await _llm.ainvoke(
        [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Plan:\n{plan_str}\n\nUser message: {user_input}",
            },
        ]
    )
    intent = response.content.strip().lower()
    if intent not in ("approve", "change", "cancel"):
        logger.warning(
            "orchestrator: unexpected intent %r, defaulting to 'change'", intent
        )
        intent = "change"
    return intent


async def orchestrator(state: OrchestratorState) -> dict | Command:
    job_id = state["job_id"]
    plan = state.get("plan", [])
    store = get_or_create_store(job_id)

    context = ""
    try:
        docs = await store.asimilarity_search(
            query=f"analysis plan approval {state.get('concern', '')}",
            k=_TOP_K,
        )
        if docs:
            context = "\n---\n".join(d.page_content for d in docs)
    except Exception:
        logger.warning("orchestrator: vector store retrieval failed, skipping context")

    assistant_msg = await _present_plan(plan, state, context)

    proposal_created_at = datetime.now(UTC).isoformat()
    dao = JobDAO()
    await dao.push_proposal(
        job_id,
        {
            "created_at": proposal_created_at,
            "plan": plan,
            "assistant_message": assistant_msg,
        },
    )

    user_input: str = interrupt(
        {
            "plan": plan,
            "assistant_message": assistant_msg,
            "discovery_summary": state.get("discovery_summary", ""),
            "total_components": len(state.get("sbom_cyclonedx", {}).get("components", [])),
        }
    )

    try:
        await store.aadd_texts([f"Assistant: {assistant_msg}", f"User: {user_input}"])
    except Exception:
        logger.warning("orchestrator: failed to add messages to vector store")

    intent = await _classify_intent(plan, user_input)
    logger.info("orchestrator: job=%s intent=%r plan=%s", job_id, intent, plan)

    await dao.update_proposal(
        job_id,
        created_at=proposal_created_at,
        user_response=user_input,
        intent=intent,
    )

    new_messages = [
        AIMessage(content=assistant_msg),
        HumanMessage(content=user_input),
    ]

    if intent == "approve":
        new_messages.append(
            AIMessage(
                content=(
                    "Plan approved! Execution is starting now. "
                    "You will be redirected to the execution detail page shortly."
                )
            )
        )
        return {"plan": plan, "messages": new_messages}

    if intent == "cancel":
        return Command(
            goto=END,
            update={"plan": [], "messages": new_messages, "cancelled": True},
        )

    # "change" — re-run planner with user's instructions
    return Command(
        goto="planner",
        update={"extra_instructions": user_input, "messages": new_messages},
    )
