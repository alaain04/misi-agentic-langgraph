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

    # Set by nodes
    repo_path: NotRequired[str]
    commit_sha: NotRequired[str]
    package_manager: NotRequired[str]
    package_manager_version: NotRequired[str]
    node_version: NotRequired[str]
    docker_node_image: NotRequired[str]
    manifest_files: NotRequired[list[str]]

    # Outputs
    discovery_error: NotRequired[str | None]

    # New output: ID written back to MainState
    prep_result_id: NotRequired[str]
