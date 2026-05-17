"""State schema for the Runtime subgraph."""

from typing import NotRequired

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RuntimeState(AnalysisState):
    result_id: NotRequired[str]
