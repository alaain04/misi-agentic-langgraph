"""State schema for the Registry subgraph."""

from typing import Any

from typing_extensions import TypedDict

from src.graphs.project_discovery.state import DependencyEntry


class RegistryState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    discovery_summary: str
    concern: str

    # ── Output ──────────────────────────────────────────────────────────────
    registry_result: dict[str, Any]
