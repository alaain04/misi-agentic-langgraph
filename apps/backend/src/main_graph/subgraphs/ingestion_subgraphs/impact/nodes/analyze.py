"""Impact analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.subgraphs.ingestion_subgraphs.impact.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.impact.state import ImpactState


async def analyze(state: ImpactState, config: RunnableConfig) -> dict:
    from src.main_graph.config import get_services
    svc = get_services(config)
    return await analyze_service(state, svc["ingestion_daos"]["impact"])
