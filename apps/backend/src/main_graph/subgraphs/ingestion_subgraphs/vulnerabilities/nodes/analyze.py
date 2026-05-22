"""Vulnerabilities analysis node."""

from langchain_core.runnables import RunnableConfig

from src.main_graph.config import get_services
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.service import (
    analyze_service,
)
from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.state import (
    VulnerabilitiesState,
)


async def analyze(state: VulnerabilitiesState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    return await analyze_service(
        state,
        svc["container"],
        svc["ingestion_daos"]["vulnerabilities"],
    )
