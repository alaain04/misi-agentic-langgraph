"""State schema for the Repo subgraph."""

from typing import Any

from typing_extensions import TypedDict

from src.graphs.project_discovery.state import DependencyEntry


class RepoState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    discovery_summary: str
    concern: str

    # ── Output ──────────────────────────────────────────────────────────────
    repo_result: dict[str, Any]
