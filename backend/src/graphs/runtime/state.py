"""State schema for the Runtime subgraph."""

from typing import Any

from typing_extensions import TypedDict

from src.graphs.project_discovery.state import DependencyEntry


class RuntimeState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    discovery_summary: str
    concern: str

    # ── Output ──────────────────────────────────────────────────────────────
    runtime_result: dict[str, Any]
