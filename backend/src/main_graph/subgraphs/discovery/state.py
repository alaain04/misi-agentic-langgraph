"""State schema for the ProjectDiscovery subgraph."""

from typing import Any, NotRequired

from typing_extensions import TypedDict


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int


class DiscoveryState(TypedDict):
    # Inputs
    repo_url: str
    concern: str
    job_id: str

    # set by clone_repository
    repo_path: NotRequired[str]

    # set by inspector_agent
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]  # "npm" | "yarn" | "pnpm"
    lock_file_missing: NotRequired[bool]
    docker_image: NotRequired[str]  # e.g. "node:22-alpine"
    install_command: NotRequired[str]  # e.g. "npm install"

    # set by lock_generator_agent
    lock_generation_attempts: NotRequired[int]
    lock_generation_error: NotRequired[str | None]

    # set by generate_sbom
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # outputs
    project_metadata: NotRequired[ProjectMetadata]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
