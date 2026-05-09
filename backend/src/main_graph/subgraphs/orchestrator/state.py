# backend/src/main_graph/subgraphs/orchestrator/state.py
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class OrchestratorState(TypedDict):
    # ── Inputs from parent graph ─────────────────────────────────────────────
    concern: str
    sbom_cyclonedx: dict[str, Any]
    discovery_summary: str
    job_id: str

    # ── Outputs to parent graph ──────────────────────────────────────────────
    messages: Annotated[list, add_messages]
    plan: NotRequired[list[str]]
    cancelled: NotRequired[bool]

    # ── Internal: orchestrator → planner on "change" ─────────────────────────
    extra_instructions: NotRequired[str]
