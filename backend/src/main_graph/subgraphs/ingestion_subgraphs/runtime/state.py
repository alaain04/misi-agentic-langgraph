"""State schema for the Runtime subgraph."""

from typing import Any

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class RuntimeState(AnalysisState):
    runtime_result: dict[str, Any]
