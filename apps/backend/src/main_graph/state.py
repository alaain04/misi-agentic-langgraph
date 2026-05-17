# backend/src/main_graph/state.py
"""State schemas for the main graph."""

import operator
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import ProjectMetadata


class MainState(TypedDict):
    # ── Inputs (provided by job_runner) ─────────────────────────────────────
    repo_url: str
    concern: str
    job_id: str

    # ── Discovery outputs ────────────────────────────────────────────────────
    repo_path: NotRequired[str]
    project_metadata: NotRequired[ProjectMetadata]
    manifest_files: NotRequired[list[str]]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # ── Orchestrator: conversation history ───────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Orchestrator: approved plan ──────────────────────────────────────────
    plan: NotRequired[list[str]]

    # ── Staged execution ─────────────────────────────────────────────────────
    execution_stages: NotRequired[list[list[str]]]
    current_stage_index: NotRequired[int]

    # ── Parallel reducer ─────────────────────────────────────────────────────
    subgraph_results: Annotated[list[dict], operator.add]

    # ── Temp fields set by task_dispatcher via Send ──────────────────────────
    subgraph_name: NotRequired[str]
    upstream_results: NotRequired[dict]

    # ── Cross-analyzer and report-reviewer outputs ────────────────────────────
    analysis_report: NotRequired[dict[str, Any]]
    reviewer_feedback: NotRequired[str]
    review_approved: NotRequired[bool]
    review_iterations: NotRequired[int]
    cancelled: NotRequired[bool]
