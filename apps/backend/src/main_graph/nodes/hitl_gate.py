"""HITL gate node — pause for user input or pass through in autopilot."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from src.main_graph.config import get_services
from src.main_graph.state import MainState

_HITL_GATE = "hitl_gate"

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def _finalize_summary(state: MainState) -> str:
    findings = state.get("findings") or []
    if not findings:
        return "Investigation complete with no findings. Proceed to generate the report?"

    seen: set[tuple] = set()
    unique = []
    for f in findings:
        key = (f.dep_name, f.severity.lower(), f.description)
        if key not in seen:
            seen.add(key)
            unique.append(f)

    counts = Counter(f.severity.lower() for f in unique)
    count_line = " | ".join(
        f"{s.upper()}: {counts[s]}" for s in _SEVERITY_ORDER if counts.get(s)
    )

    notable = [f for f in unique if f.severity.lower() in ("critical", "high")]
    lines = "\n".join(f"- [{f.severity.upper()}] {f.dep_name}: {f.description}" for f in notable)

    summary = f"Investigation complete. {len(unique)} unique findings — {count_line}."
    if lines:
        summary += f"\n\nKey findings:\n{lines}"
    summary += "\n\nProceed to generate the report?"
    return summary


async def hitl_gate(state: MainState, config: RunnableConfig) -> dict:
    """Interrupt the graph to ask the user a question, or pass through in autopilot."""
    decision = state.get("conductor_decision")
    if decision is None:
        return {}

    autopilot = state.get("autopilot", False)

    if decision.ask_user or decision.checkpoint_message:
        question = decision.ask_user or decision.checkpoint_message
        msg_type = "ask_user" if decision.ask_user else "checkpoint"
    elif decision.finalize:
        question = _finalize_summary(state)
        msg_type = "checkpoint"
    else:
        return {}

    if autopilot:
        return {}

    job_id = state["job_id"]
    svc = get_services(config)
    dao = svc["job_repo"]

    user_reply: str = interrupt({"question": question, "type": msg_type})

    await dao.push_artifact_message(job_id, _HITL_GATE, {
        "role": "human",
        "content": user_reply,
        "created_at": datetime.now(UTC).isoformat(),
    })

    logger.info("hitl_gate: job=%s resumed with user reply", job_id)
    return {"messages": [AIMessage(content=question), HumanMessage(content=user_reply)]}
