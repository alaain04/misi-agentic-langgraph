from typing_extensions import TypedDict

from src.main_graph.subgraphs.discovery.state import DependencyEntry


class AnalysisState(TypedDict):
    direct_dependencies: list[DependencyEntry]
    transitive_dependencies: list[DependencyEntry]
    discovery_summary: str
    concern: str
