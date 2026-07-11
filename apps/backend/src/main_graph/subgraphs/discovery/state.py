"""State schema for the discovery subgraph."""

from typing import NotRequired

from typing_extensions import TypedDict


class ProjectMetadata(TypedDict):
    name: str
    package_manager: str
    direct_dependencies_count: int
    transitive_dependencies_count: int


class DiscoveryState(TypedDict):
    # Inputs
    job_id: str
    repo_url: str
    concern: str
    autopilot: bool

    # Set by nodes
    repo_path: NotRequired[str]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    package_manager_version: NotRequired[str]
    has_lock_file: NotRequired[bool]
    docker_image: NotRequired[str]

    # Outputs
    project_metadata: NotRequired[ProjectMetadata]
    project_context: NotRequired[str]
    discovery_error: NotRequired[str | None]
