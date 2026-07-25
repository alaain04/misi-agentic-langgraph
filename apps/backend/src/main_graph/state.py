from __future__ import annotations

from typing import Annotated, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class MainState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str
    autopilot: bool

    # Inter-layer result IDs
    prep_result_id: NotRequired[str]
    analysis_result_id: NotRequired[str]
    remediation_result_id: NotRequired[str]
    report_result_id: NotRequired[str]

    # Control
    messages: Annotated[list, add_messages]
    cancelled: NotRequired[bool]
    discovery_error: NotRequired[str | None]
