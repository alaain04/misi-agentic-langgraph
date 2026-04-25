"""Orchestrator node — LLM agent that coordinates planning via conversational loop."""

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.graphs.main_graph.nodes.planner import run_planner
from src.graphs.main_graph.state import MainState
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


async def _present_plan(plan: list[str], state: MainState, context: str) -> str:
    """Use LLM to generate a natural-language presentation of the plan."""
    plan_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan))
    user_content = (
        f"Project concern: {state.get('concern', 'not specified')}\n"
        f"Direct dependencies: {len(state.get('direct_dependencies', []))}\n"
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
    """Classify user message as 'approve', 'change', or 'cancel'."""
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


async def orchestrator(state: MainState) -> dict | Command:
    """\
    Conversational orchestrator that loops until the user approves or cancels the plan.

    On first entry, generates an initial plan via run_planner().
    On subsequent iterations (after a 'change' response), re-generates the plan
    with the user's instructions as extra context.

    Stores conversation turns in a per-job vector store for semantic retrieval.
    """
    job_id = state["job_id"]
    store = get_or_create_store(job_id)

    # Generate initial plan (no extra instructions on first call)
    plan = await run_planner(state)

    while True:
        # Retrieve relevant context from prior conversation turns
        context = ""
        try:
            docs = await store.asimilarity_search(
                query=f"analysis plan approval {state.get('concern', '')}",
                k=_TOP_K,
            )
            if docs:
                context = "\n---\n".join(d.page_content for d in docs)
        except Exception:
            logger.warning(
                "orchestrator: vector store retrieval failed, skipping context"
            )

        # Generate assistant message presenting the plan
        assistant_msg = await _present_plan(plan, state, context)

        # Pause — the interrupt payload is surfaced to the API and stored in MongoDB
        user_input: str = interrupt(
            {
                "plan": plan,
                "assistant_message": assistant_msg,
                "discovery_summary": state.get("discovery_summary", ""),
                "direct_dependencies_count": len(state.get("direct_dependencies", [])),
            }
        )

        # Post-interrupt: embed both turns into the vector store
        try:
            await store.aadd_texts(
                [f"Assistant: {assistant_msg}", f"User: {user_input}"]
            )
        except Exception:
            logger.warning("orchestrator: failed to add messages to vector store")

        # Classify user intent
        intent = await _classify_intent(plan, user_input)
        logger.info("orchestrator: job=%s intent=%r plan=%s", job_id, intent, plan)

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
            return Command(goto=END, update={"plan": [], "messages": new_messages})

        # "change" — re-plan with the user's instructions, then loop
        plan = await run_planner(state, extra_instructions=user_input)
