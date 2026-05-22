"""Registry analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.registry.service import analyze_service
from src.main_graph.subgraphs.ingestion_subgraphs.registry.state import RegistryState


async def analyze(state: RegistryState, config: RunnableConfig) -> dict:
    """Analyze registry metadata for a dependency."""
    svc = get_services(config)
    return await analyze_service(state, svc["ingestion_daos"]["registry"])
