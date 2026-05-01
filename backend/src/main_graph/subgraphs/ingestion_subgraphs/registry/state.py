"""State schema for the Registry subgraph."""

from typing import Any

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RegistryState(AnalysisState):
    registry_result: dict[str, Any]
