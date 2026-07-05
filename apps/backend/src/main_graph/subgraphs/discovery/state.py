"""State schema for the discovery subgraph."""

import operator
from typing import Annotated, Any, NotRequired

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

    # set by discovery nodes
    repo_path: NotRequired[str]
    manifest_files: NotRequired[list[str]]
    detected_package_manager: NotRequired[str]
    package_manager_version: NotRequired[str]
    has_lock_file: NotRequired[bool]
    docker_image: NotRequired[str]
    sbom_cyclonedx: NotRequired[dict[str, Any]]
    sbom_result_id: NotRequired[str]
    sbom_error: NotRequired[str | None]

    # outputs
    project_metadata: NotRequired[ProjectMetadata]
    discovery_summary: NotRequired[str]
    discovery_error: NotRequired[str | None]
    discovery_steps: Annotated[list[str], operator.add]
