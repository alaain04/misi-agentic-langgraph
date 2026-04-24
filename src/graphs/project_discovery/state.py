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


class DiscoveryState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────
    package_json_content: str
    lock_file_content: str
    lock_file_name: str  # e.g. "package-lock.json" | "yarn.lock" | "pnpm-lock.yaml"
    concern: str

    # ── Internal: parse_package_files ───────────────────────────────────
    # file name → structured parse result
    parsed_manifests: dict[str, Any]

    # ── Outputs ─────────────────────────────────────────────────────────
    project_metadata: ProjectMetadata
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    dependency_tree: dict[str, Any]
    manifest_files: list[str]
    discovery_summary: str
    discovery_error: NotRequired[str | None]
