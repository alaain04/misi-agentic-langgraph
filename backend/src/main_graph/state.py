"""State schemas for the main graph."""

import operator
from typing import Annotated, Any, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import DependencyEntry, ProjectMetadata


class MainState(TypedDict):
    # ── Inputs (provided by job_runner) ─────────────────────────────────────
    package_json_content: str
    lock_file_content: str
    lock_file_name: str
    concern: str
    job_id: str

    # ── Discovery outputs (superset of DiscoveryState) ───────────────────────
    parsed_manifests: dict[str, Any]
    project_metadata: ProjectMetadata
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    dependency_tree: dict[str, Any]
    manifest_files: list[str]
    discovery_summary: str
    discovery_error: NotRequired[str | None]

    # ── Orchestrator: conversation history ───────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Orchestrator: approved plan ──────────────────────────────────────────
    plan: NotRequired[list[str]]

    # ── Staged execution (resolved from dependency declarations) ─────────────
    execution_stages: NotRequired[list[list[str]]]
    current_stage_index: NotRequired[int]

    # ── Parallel reducer: each Send-spawned execute_plan appends one item ────
    subgraph_results: Annotated[list[dict], operator.add]

    # ── Temp fields set by task_dispatcher via Send ──────────────────────────
    subgraph_name: NotRequired[str]
    upstream_results: NotRequired[dict]

    # ── Terminal node outputs ─────────────────────────────────────────────────
    summary: NotRequired[str]
    review: NotRequired[str]
    recommendation: NotRequired[str]
    cancelled: NotRequired[bool]
