# backend/src/main_graph/subgraphs/discovery/state.py
"""State schema for the ProjectDiscovery subgraph."""

from typing import Any, NotRequired

from typing_extensions import TypedDict


class DependencyEntry(TypedDict):
    name: str
    version_spec: str


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int


class DiscoveryState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────
    repo_url: str
    concern: str

    # ── Internal: set by fetch_repository ───────────────────────────────
    repo_path: NotRequired[str]

    # ── Internal: set by generate_sbom ──────────────────────────────────
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    manifest_files: NotRequired[list[str]]

    # ── Outputs: set by build_dependency_summary ─────────────────────────
    project_metadata: NotRequired[ProjectMetadata]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
    sbom_error: NotRequired[str | None]
