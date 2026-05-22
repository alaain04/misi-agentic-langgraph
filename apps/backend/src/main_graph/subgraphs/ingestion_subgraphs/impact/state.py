from typing import NotRequired

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class ImpactState(AnalysisState):
    result_id: NotRequired[str]
