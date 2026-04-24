"""Routing functions for the ProjectDiscovery subgraph."""

from src.graphs.project_discovery.constants import (
    BUILD_DEPENDENCY_SUMMARY,
    DETECT_MANIFEST_FILES,
)
from src.graphs.project_discovery.state import DiscoveryState


def after_fetch_repository(state: DiscoveryState) -> str:
    """Short-circuit to the summary node when a fatal error occurred."""
    if state.get("discovery_error"):
        return BUILD_DEPENDENCY_SUMMARY
    return DETECT_MANIFEST_FILES
