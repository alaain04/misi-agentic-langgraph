from typing import NotRequired

from src.main_graph.subgraphs.ingestion_subgraphs._base import AnalysisState


class SupplyChainState(AnalysisState):
    result_id: NotRequired[str]
