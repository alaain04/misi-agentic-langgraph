from typing import Annotated, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import DependencyEntry


class OrchestratorState(TypedDict):
    # ── Inputs from parent graph ─────────────────────────────────────────────
    concern: str
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    discovery_summary: str
    job_id: str

    # ── Outputs to parent graph ──────────────────────────────────────────────
    messages: Annotated[list, add_messages]
    plan: NotRequired[list[str]]
    cancelled: NotRequired[bool]

    # ── Internal: orchestrator → planner on "change" ─────────────────────────
    extra_instructions: NotRequired[str]
