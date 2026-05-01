"""State schema for the Repo subgraph."""

from typing import Any

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RepoState(AnalysisState):
    repo_result: dict[str, Any]
