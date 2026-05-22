"""Runtime analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.subgraphs.ingestion_subgraphs.runtime.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.runtime.state import RuntimeState


async def analyze(state: RuntimeState, config: RunnableConfig) -> dict:
    from src.main_graph.config import get_services
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["ingestion_daos"]["runtime"],
        svc["runtime_cache_dao"],
    )
