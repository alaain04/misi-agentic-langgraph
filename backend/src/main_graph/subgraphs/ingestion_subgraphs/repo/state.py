"""State schema for the Repo subgraph."""

from typing import NotRequired

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RepoState(AnalysisState):
    result_id: NotRequired[str]
