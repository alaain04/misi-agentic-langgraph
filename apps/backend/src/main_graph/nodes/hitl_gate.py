"""HITL gate node — pause for user input or pass through in autopilot."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from src.main_graph.config import get_services
from src.main_graph.constants import HITL_GATE
from src.main_graph.state import MainState

logger = logging.getLogger(__name__)


async def hitl_gate(state: MainState, config: RunnableConfig) -> dict:
    """Interrupt the graph to ask the user a question, or pass through in autopilot."""
    decision = state.get("conductor_decision")
    if decision is None:
        return {}

    autopilot = state.get("autopilot", False)
    question = decision.ask_user or decision.checkpoint_message

    if not question or autopilot:
        return {}

    job_id = state["job_id"]
    svc = get_services(config)
    dao = svc["job_repo"]

    created_at = datetime.now(UTC).isoformat()
    await dao.push_artifact_message(job_id, HITL_GATE, {
        "role": "assistant",
        "content": question,
        "created_at": created_at,
        "type": "ask_user" if decision.ask_user else "checkpoint",
    })

    user_reply: str = interrupt({"question": question, "type": "ask_user" if decision.ask_user else "checkpoint"})

    await dao.push_artifact_message(job_id, HITL_GATE, {
        "role": "human",
        "content": user_reply,
        "created_at": datetime.now(UTC).isoformat(),
    })

    logger.info("hitl_gate: job=%s resumed with user reply", job_id)
    return {"messages": [AIMessage(content=question), HumanMessage(content=user_reply)]}
