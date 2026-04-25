"""State schema for the RiskScore subgraph."""

from typing import Any

from typing_extensions import TypedDict

from src.graphs.project_discovery.state import DependencyEntry


class RiskScoreState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────────────────────
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    discovery_summary: str
    concern: str

    # ── Output ──────────────────────────────────────────────────────────────
    risk_score_result: dict[str, Any]
