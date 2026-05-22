# backend/src/main_graph/state.py
"""State schemas for the main graph."""

import operator
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.plan import Plan
from src.main_graph.subgraphs.discovery.state import ProjectMetadata


class MainState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
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

    # ── Orchestrator ─────────────────────────────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Approved plan ────────────────────────────────────────────────────────
    plan: NotRequired[Plan]
    dep_filter: NotRequired[list[str] | None]

    # ── Staged execution ─────────────────────────────────────────────────────
    execution_stages: NotRequired[list[list[dict]]]
    current_stage_index: NotRequired[int]

    # ── Parallel reducer ─────────────────────────────────────────────────────
    subgraph_results: Annotated[list[dict], operator.add]

    # ── Temp fields set via Send ─────────────────────────────────────────────
    subgraph_name: NotRequired[str]
    dep_name: NotRequired[str | None]
    upstream_results: NotRequired[dict]

    # ── Stage 1→2 gate ───────────────────────────────────────────────────────
    risk_ranker_done: NotRequired[bool]
    risk_rankings: NotRequired[list[dict]]
    high_risk_deps: NotRequired[list[str]]

    # ── Stage 3 outputs ──────────────────────────────────────────────────────
    risk_scores: NotRequired[list[dict]]
    recommendations: NotRequired[list[dict]]

    # ── Report ───────────────────────────────────────────────────────────────
    analysis_report: NotRequired[dict[str, Any]]
    reviewer_feedback: NotRequired[str]
    review_approved: NotRequired[bool]
    review_iterations: NotRequired[int]
    cancelled: NotRequired[bool]
