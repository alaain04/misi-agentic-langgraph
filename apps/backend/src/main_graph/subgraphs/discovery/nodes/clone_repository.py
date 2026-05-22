"""Node: clone_repository."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.service import clone_repository_service
from src.main_graph.subgraphs.discovery.state import DiscoveryState


async def clone_repository(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await clone_repository_service(state, svc["container"])
