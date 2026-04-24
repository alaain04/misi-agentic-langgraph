"""State schema for the ProjectDiscovery subgraph."""

from typing import Any, NotRequired

from typing_extensions import TypedDict


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int


class DependencyEntry(TypedDict):
    name: str
    version_spec: str
    is_dev: bool


class DiscoveryState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────
    repo_url: str
    concern: str
    token: NotRequired[str | None]  # GitHub personal access token

    # ── Internal: fetch_repository ───────────────────────────────────────
    repo_owner: str
    repo_name: str
    default_branch: str

    # ── Internal: detect_manifest_files ─────────────────────────────────
    # Relative paths of JS package-manager files found in the repository
    detected_files: list[str]

    # ── Internal: parse_package_files ───────────────────────────────────
    # file path → structured parse result
    parsed_manifests: dict[str, Any]

    # ── Outputs ─────────────────────────────────────────────────────────
    project_metadata: ProjectMetadata
    direct_dependencies: list[DependencyEntry]
    manifest_files: list[str]
    discovery_summary: str
    discovery_error: NotRequired[str | None]
