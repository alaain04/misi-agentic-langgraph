import operator
from typing import Annotated, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import ProjectMetadata
from src.models.conductor import ConductorDecision, FindingNote, ToolResult


class MainState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str
    autopilot: bool

    # Prep outputs
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    project_context: NotRequired[str]
    discovery_error: NotRequired[str | None]

    # Conductor loop
    conductor_decision: NotRequired[ConductorDecision]
    tool_results: Annotated[list[ToolResult], operator.add]
    findings: Annotated[list[FindingNote], operator.add]
    conductor_iteration: NotRequired[int]
    messages: Annotated[list, add_messages]

    # Output
    analysis_report: NotRequired[dict]
    cancelled: NotRequired[bool]
