"""Node: generate_sbom."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.service import generate_sbom_service
from src.main_graph.subgraphs.discovery.state import DiscoveryState


async def generate_sbom(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await generate_sbom_service(state, svc["container"], svc["sbom_dao"])
