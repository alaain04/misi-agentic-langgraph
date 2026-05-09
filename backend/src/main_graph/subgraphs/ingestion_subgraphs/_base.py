from typing import Any, NotRequired

from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import DependencyEntry


class AnalysisState(TypedDict):
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    discovery_summary: str
    concern: str
    upstream_results: NotRequired[dict[str, Any]]
    repo_path: NotRequired[str]  # cloned repo temp dir, consumed by trivy_scan
