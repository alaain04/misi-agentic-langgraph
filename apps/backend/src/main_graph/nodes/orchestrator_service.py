"""Orchestrator business logic — pure service, no infrastructure wiring."""

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.domain.ports.job_repository_port import JobRepositoryPort
from src.domain.ports.vector_store_port import VectorStorePort
from src.main_graph.nodes.planner import _PIPELINE_SUBGRAPHS, run_planner
from src.main_graph.plan import Plan
from src.main_graph.state import MainState
from src.main_graph.subgraphs.ingestion_subgraphs import SUBGRAPH_DESCRIPTIONS
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_4O_MINI)

# Build a name → description lookup from all known subgraphs.
_SUBGRAPH_DESC: dict[str, str] = {}
for _entry in SUBGRAPH_DESCRIPTIONS:
    _name, _desc = _entry.split(":", 1)
    _SUBGRAPH_DESC[_name.strip()] = _desc.strip()
for _name, _desc in _PIPELINE_SUBGRAPHS:
    _SUBGRAPH_DESC[_name] = _desc

_INTENT_SYSTEM_PROMPT = """\
You are classifying a user's response to a proposed dependency analysis plan.
The user was shown the plan and asked whether to proceed, request changes, or cancel.

Classify their intent as exactly one of:
  - approve: user is satisfied and wants to proceed
  - change: user wants modifications, has concerns, or provides new instructions
  - cancel: user wants to abort the analysis entirely

Return ONLY one word: approve, change, or cancel.
"""


def _present_plan(plan: Plan) -> str:
    """Build a deterministic presentation of the plan."""
    subgraphs = plan.get("subgraphs", []) if isinstance(plan, dict) else list(plan)
    dep_filter = plan.get("dep_filter") if isinstance(plan, dict) else None
    lines = ["**Proposed Analysis Plan:**\n"]
    for i, name in enumerate(subgraphs, 1):
        desc = _SUBGRAPH_DESC.get(name, name)
        lines.append(f"{i}. **{name}**: {desc}")
    if dep_filter:
        lines.append(f"\n**Scope:** {', '.join(dep_filter)}")
    lines.append(
        "\nWould you like to proceed with this plan, request changes, or cancel?"
    )
    return "\n".join(lines)


async def _classify_intent(plan: Plan, user_input: str) -> str:
    """Classify user message as 'approve', 'change', or 'cancel'."""
    subgraphs = plan.get("subgraphs", []) if isinstance(plan, dict) else list(plan)
    plan_str = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(subgraphs))
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


async def orchestrator_service(
    state: MainState,
    dao: JobRepositoryPort,
    vector_store: VectorStorePort,
) -> dict | Command:
    """\
    Conversational orchestrator that loops until the user approves or cancels the plan.

    On first entry, generates an initial plan via run_planner().
    On subsequent iterations (after a 'change' response), re-generates the plan
    with the user's instructions as extra context.
    """
    job_id = state["job_id"]

    # Generate initial plan (no extra instructions on first call)
    plan = await run_planner(state)

    while True:
        # Generate assistant message presenting the plan
        assistant_msg = _present_plan(plan)

        # Push partial proposal now so PlanPage can read it during awaiting_approval
        proposal_created_at = datetime.now(UTC).isoformat()
        await dao.push_proposal(
            job_id,
            {
                "created_at": proposal_created_at,
                "plan": plan,
                "assistant_message": assistant_msg,
            },
        )

        # Pause — graph suspends here until the user responds
        user_input: str = interrupt(
            {
                "plan": plan,
                "assistant_message": assistant_msg,
                "discovery_summary": state.get("discovery_summary", ""),
                "components_count": len(
                    state.get("sbom_cyclonedx", {}).get("components", [])
                ),
            }
        )

        # Post-interrupt: embed both turns into the vector store
        try:
            await vector_store.add_texts(
                [f"Assistant: {assistant_msg}", f"User: {user_input}"]
            )
        except Exception:
            logger.warning("orchestrator: failed to add messages to vector store")

        # Classify user intent
        intent = await _classify_intent(plan, user_input)
        logger.info("orchestrator: job=%s intent=%r plan=%s", job_id, intent, plan)

        # Complete the proposal with the user's response and classified intent
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

        # "change" — re-plan with the user's instructions, then loop
        plan = await run_planner(state, extra_instructions=user_input)
